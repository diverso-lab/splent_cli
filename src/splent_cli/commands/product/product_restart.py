"""
product:restart — Restart the active product's Flask app inside the container.

Detects feature changes in pyproject.toml (added, removed, or mode changes)
and reinstalls affected features before restarting Flask.
"""

import os
import subprocess
import tomllib

import click

from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import parse_feature_entry, read_features_from_data


# ── Feature change detection ─────────────────────────────────────────


def _installed_features(container_id: str) -> dict[str, str]:
    """Read pip-installed splent_feature_* packages from the container.

    Returns {package_name: version_or_path} dict.
    """
    result = subprocess.run(
        ["docker", "exec", container_id, "pip", "list", "--format=json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}

    import json

    installed = {}
    try:
        for pkg in json.loads(result.stdout):
            name = pkg.get("name", "")
            if name.startswith("splent-feature-") or name.startswith("splent_feature_"):
                # Normalize: pip uses hyphens, we use underscores
                normalized = name.replace("-", "_")
                installed[normalized] = pkg.get("version", "")
    except (json.JSONDecodeError, KeyError):
        pass
    return installed


def _declared_features(workspace: str, product: str) -> list[dict]:
    """Read features from pyproject.toml with their mode (editable vs pinned)."""
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.isfile(pyproject_path):
        return []

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    env = os.getenv("SPLENT_ENV", "dev")
    entries = read_features_from_data(data, env)

    result = []
    for entry in entries:
        ns, name, version = parse_feature_entry(entry)
        editable = version is None
        result.append(
            {
                "entry": entry,
                "name": name,
                "version": version,
                "editable": editable,
            }
        )
    return result


def _detect_changes(container_id, workspace, product):
    """Compare declared features with installed ones. Returns lists of features to install."""
    installed = _installed_features(container_id)
    declared = _declared_features(workspace, product)

    to_install = []  # (name, path_or_entry, editable)

    for feat in declared:
        name = feat["name"]
        editable = feat["editable"]

        if editable:
            # Editable: check if it's installed as editable (pip shows local path)
            feature_path = os.path.join(workspace, name)
            if name not in installed or not os.path.isdir(feature_path):
                to_install.append((name, feature_path, True))
        else:
            # Pinned: check if version matches
            if name not in installed:
                to_install.append((name, feat["entry"], False))

    return to_install


def _install_features(container_id, to_install, workspace, product):
    """Install changed features in the container."""
    for name, path_or_entry, editable in to_install:
        short = name.replace("splent_feature_", "")
        if editable:
            # pip install -e from workspace
            feature_path = f"/workspace/{name}"
            click.echo(
                click.style("  install ", dim=True)
                + f"{short}"
                + click.style(" (editable)", fg="cyan")
            )
            subprocess.run(
                [
                    "docker",
                    "exec",
                    container_id,
                    "pip",
                    "install",
                    "-e",
                    feature_path,
                    "-q",
                ],
                capture_output=True,
            )
        else:
            # pip install from symlink (pinned)
            ns, name, version = parse_feature_entry(path_or_entry)
            link_dir = f"/workspace/{product}/features/{ns}"
            link_path = f"{link_dir}/{name}@{version}"
            click.echo(
                click.style("  install ", dim=True)
                + f"{short}"
                + click.style(f" @{version}", dim=True)
            )
            subprocess.run(
                [
                    "docker",
                    "exec",
                    container_id,
                    "pip",
                    "install",
                    "-e",
                    link_path,
                    "-q",
                ],
                capture_output=True,
            )


# ── Command ──────────────────────────────────────────────────────────


@click.command(
    "product:restart",
    short_help="Restart the active product's Flask app.",
)
@click.option("--dev", "env_dev", is_flag=True, help="Restart development.")
@click.option("--prod", "env_prod", is_flag=True, help="Restart production.")
@click.option(
    "--full",
    is_flag=True,
    help="Re-run the full entrypoint (reinstall deps, migrations, etc.).",
)
def product_restart(env_dev, env_prod, full):
    """Restart Flask inside the container.

    \b
    Detects feature changes before restarting:
    - New features in pyproject.toml → pip install
    - Features switched to editable → pip install -e
    Then kills Flask/watchmedo and restarts.

    \b
    Use --full to re-run the entire entrypoint (reinstall all deps,
    migrations, etc.).
    """
    product = context.require_app()
    workspace = str(context.workspace())
    env = context.resolve_env(env_dev, env_prod)
    product_path = os.path.join(workspace, product)

    compose_file = compose.resolve_file(product_path, env)
    if not compose_file:
        click.secho(f"  No docker-compose file found for {product} ({env})", fg="red")
        raise SystemExit(1)

    docker_dir = os.path.join(product_path, "docker")
    project_name = compose.project_name(product, env)

    container_id = compose.find_main_container(project_name, compose_file, docker_dir)
    if not container_id:
        click.secho(f"  No running container found for {product} ({env})", fg="red")
        raise SystemExit(1)

    # ── Resolve symlinks + detect and install changed features ──
    if not full:
        # Always resolve symlinks — catches pinned→editable switches
        from splent_cli.commands.product.product_resolve import product_sync

        ctx = click.get_current_context()
        ctx.invoke(product_sync, force=False)

        to_install = _detect_changes(container_id, workspace, product)
        if to_install:
            click.echo(
                click.style("  detected ", dim=True)
                + f"{len(to_install)} feature(s) to install"
            )
            _install_features(container_id, to_install, workspace, product)
            click.echo()

    # ── Kill existing processes ───────────────────────────────────
    subprocess.run(
        [
            "docker",
            "exec",
            container_id,
            "bash",
            "-c",
            "pkill -f 'flask run' ; pkill -f watchmedo ; pkill -f gunicorn ; sleep 1",
        ],
        capture_output=True,
    )

    if full:
        click.echo(
            click.style("  restarting ", dim=True)
            + f"{product} ({env}) — full entrypoint"
        )
        container_entrypoint = f"/workspace/{product}/entrypoints/entrypoint.{env}.sh"
        source_cmd = f"set -a && . /workspace/{product}/docker/.env && set +a && bash {container_entrypoint}"
        subprocess.run(
            ["docker", "exec", "-d", container_id, "bash", "-c", source_cmd],
            capture_output=True,
        )
    else:
        click.echo(click.style("  restarting ", dim=True) + f"{product} ({env})")
        start_script = f"/workspace/{product}/scripts/05_0_start_app_{env}.sh"
        source_cmd = f"set -a && . /workspace/{product}/docker/.env && set +a && bash {start_script}"
        subprocess.run(
            ["docker", "exec", "-d", container_id, "bash", "-c", source_cmd],
            capture_output=True,
        )

    click.secho("  done.", fg="green")


cli_command = product_restart
