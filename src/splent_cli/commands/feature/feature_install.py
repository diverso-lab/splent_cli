import os
import re
import subprocess
import tomllib
import click
import requests
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data


def _get_required_features(feature_pyproject: str) -> list[str]:
    """Read [tool.splent.contract.requires].features from a feature's pyproject.toml."""
    if not os.path.isfile(feature_pyproject):
        return []
    with open(feature_pyproject, "rb") as f:
        data = tomllib.load(f)
    return (
        data.get("tool", {})
        .get("splent", {})
        .get("contract", {})
        .get("requires", {})
        .get("features", [])
    )


def _get_product_feature_shorts(
    workspace: str, product: str, env_name: str
) -> set[str]:
    """Return short names of all features currently in the product."""
    py_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.isfile(py_path):
        return set()
    with open(py_path, "rb") as f:
        data = tomllib.load(f)
    entries = read_features_from_data(data, env_name)
    shorts = set()
    for entry in entries:
        name = entry.split("/")[-1] if "/" in entry else entry
        name = name.split("@")[0]
        name = name.replace("splent_feature_", "")
        shorts.add(name)
    return shorts


def _find_feature_pyproject(
    workspace: str, feature_name: str, namespace_fs: str, version: str | None
) -> str | None:
    """Locate the pyproject.toml of a feature (editable or cached)."""
    # Editable at workspace root
    candidate = os.path.join(workspace, feature_name, "pyproject.toml")
    if os.path.isfile(candidate):
        return candidate
    # Pinned in cache
    if version:
        candidate = os.path.join(
            workspace,
            ".splent_cache",
            "features",
            namespace_fs,
            f"{feature_name}@{version}",
            "pyproject.toml",
        )
        if os.path.isfile(candidate):
            return candidate
    return None


def _get_contract_env(feature_pyproject: str) -> str | None:
    """Read [tool.splent.contract].env from a feature's pyproject.toml.

    Returns "dev", "prod", or None (all environments).
    """
    if not os.path.isfile(feature_pyproject):
        return None
    with open(feature_pyproject, "rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("splent", {}).get("contract", {}).get("env")


def _check_dependencies(workspace, product, env_name, feature_pyproject, short):
    """Check if required features are present in the product. Returns list of missing."""
    required = _get_required_features(feature_pyproject)
    if not required:
        return []
    installed = _get_product_feature_shorts(workspace, product, env_name)
    missing = [r for r in required if r not in installed]
    return missing


def _get_available_versions(namespace: str, repo: str, limit: int = 10) -> list[str]:
    """Fetch semver tags from GitHub, sorted newest first."""
    api_url = f"https://api.github.com/repos/{namespace}/{repo}/tags?per_page=100"
    try:
        r = requests.get(api_url, timeout=5)
        r.raise_for_status()
        tags = r.json()
        versions = []
        for tag in tags:
            name = tag.get("name", "")
            m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", name)
            if m:
                versions.append(
                    (int(m.group(1)), int(m.group(2)), int(m.group(3)), name)
                )
        versions.sort(reverse=True)
        return [v[3] for v in versions[:limit]]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return []


@click.command(
    "feature:install",
    short_help="Install a feature into the active product (interactive).",
)
@click.argument("feature_identifier", required=True)
@click.option(
    "--dev",
    "env_scope",
    flag_value="dev",
    help="Add to features_dev (development only).",
)
@click.option(
    "--prod",
    "env_scope",
    flag_value="prod",
    help="Add to features_prod (production only).",
)
@click.option(
    "--editable",
    "mode",
    flag_value="editable",
    help="Clone as editable at workspace root.",
)
@click.option(
    "--pinned",
    "mode",
    flag_value="pinned",
    help="Pin a released version from cache.",
)
@click.option(
    "--version",
    "version",
    default=None,
    help="Version to pin (e.g. v1.2.3). Only for pinned mode.",
)
def feature_install(feature_identifier, env_scope, mode, version):
    """
    Install a feature into the active product in one step.

    Combines clone + add/attach + env merge + Docker service startup.

    \b
    feature:add/feature:attach handle pyproject.toml, symlinks, and
    hot-reinstall (pip + Flask reload) in the web container. This command
    adds: clone if needed, env merge for new variables, and starting
    the feature's own Docker services (if any).

    \b
    Examples:
      splent feature:install splent-io/splent_feature_nginx
      splent feature:install splent-io/splent_feature_nginx --pinned --version v1.0.0
      splent feature:install splent-io/splent_feature_nginx --editable --dev
    """
    product = context.require_app()
    workspace = str(context.workspace())
    env_name = os.getenv("SPLENT_ENV", "dev")

    # ── Parse identifier ──────────────────────────────────────────────
    namespace, namespace_github, namespace_fs, feature_name = (
        compose.parse_feature_identifier(feature_identifier)
    )
    short = feature_name.replace("splent_feature_", "")

    # ── Ask mode if not specified ─────────────────────────────────────
    if not mode:
        editable_exists = os.path.isdir(os.path.join(workspace, feature_name))
        if editable_exists:
            click.echo(f"  {short} found at workspace root.")
            mode = "editable"
        else:
            choice = click.prompt(
                "  Install as",
                type=click.Choice(["editable", "pinned"]),
                default="pinned",
            )
            mode = choice

    click.echo()
    click.echo(
        click.style("  installing ", dim=True)
        + click.style(short, bold=True)
        + click.style(f" ({mode})", dim=True)
        + (click.style(f" [{env_scope}]", fg="yellow") if env_scope else "")
    )

    # ── Step 1: Ensure feature is available locally ───────────────────
    if mode == "pinned":
        # Resolve version
        if not version:
            click.echo(
                click.style("  version  ", dim=True) + "fetching available versions..."
            )
            versions = _get_available_versions(namespace_github, feature_name)
            if not versions:
                click.secho(
                    f"  No versions found for {namespace_github}/{feature_name}.",
                    fg="red",
                )
                raise SystemExit(1)

            # Show versions and let user pick
            click.echo()
            for i, v in enumerate(versions):
                label = click.style(" (latest)", fg="green") if i == 0 else ""
                click.echo(f"    {i + 1})  {v}{label}")
            click.echo()

            choice = click.prompt(
                "  Select version",
                type=click.IntRange(1, len(versions)),
                default=1,
            )
            version = versions[choice - 1]
            click.echo(click.style("  version  ", dim=True) + f"selected {version}")

        # Clone to cache if not present
        cache_dir = os.path.join(
            workspace,
            ".splent_cache",
            "features",
            namespace_fs,
            f"{feature_name}@{version}",
        )
        if not os.path.isdir(cache_dir):
            click.echo(click.style("  clone    ", dim=True) + f"{short}@{version}")
            result = subprocess.run(
                [
                    "splent",
                    "feature:clone",
                    f"{namespace_github}/{feature_name}@{version}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                click.secho(f"  Clone failed: {result.stderr.strip()}", fg="red")
                raise SystemExit(1)
        else:
            click.echo(
                click.style("  cache    ", dim=True)
                + f"{short}@{version} already cached"
            )

        # Check contract env and dependencies before attaching
        feat_pyproject = _find_feature_pyproject(
            workspace, feature_name, namespace_fs, version
        )
        if feat_pyproject:
            # Auto-detect env scope from contract
            if not env_scope:
                contract_env = _get_contract_env(feat_pyproject)
                if contract_env:
                    env_scope = contract_env
                    click.echo(
                        click.style("  scope    ", dim=True)
                        + f"contract declares env={contract_env} → features_{contract_env}"
                    )

            missing = _check_dependencies(
                workspace, product, env_name, feat_pyproject, short
            )
            if missing:
                click.echo()
                click.secho(
                    f"  {short} requires features not installed in {product}:",
                    fg="yellow",
                )
                for m in missing:
                    click.echo(f"    - {m}")
                click.echo()
                if not click.confirm("  Install them first?", default=True):
                    click.secho("  Aborted.", fg="red")
                    raise SystemExit(1)
                for m in missing:
                    click.echo()
                    subprocess.run(
                        [
                            "splent",
                            "feature:install",
                            f"{namespace_github}/splent_feature_{m}",
                        ],
                        text=True,
                    )

        # Attach to product
        click.echo(click.style("  attach   ", dim=True) + f"{short}@{version}")
        cmd = ["splent", "feature:attach", feature_identifier, version]
        if env_scope:
            cmd.append(f"--{env_scope}")
        subprocess.run(cmd, capture_output=True, text=True)

    elif mode == "editable":
        feature_dir = os.path.join(workspace, feature_name)
        if not os.path.isdir(feature_dir):
            # Clone to workspace root as editable
            click.echo(click.style("  clone    ", dim=True) + f"{short} (editable)")
            from splent_cli.commands.feature.feature_clone import (
                _build_repo_url,
                _get_latest_tag,
            )

            tag = _get_latest_tag(namespace_github, feature_name)
            repo_url, _ = _build_repo_url(namespace_github, feature_name)
            branch = tag or "main"
            try:
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--branch",
                        branch,
                        "--quiet",
                        repo_url,
                        feature_dir,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError:
                click.secho(
                    f"  Could not clone {namespace_github}/{feature_name}.", fg="red"
                )
                raise SystemExit(1)

        # Check contract env and dependencies before adding
        feat_pyproject = _find_feature_pyproject(
            workspace, feature_name, namespace_fs, None
        )
        if feat_pyproject:
            # Auto-detect env scope from contract
            if not env_scope:
                contract_env = _get_contract_env(feat_pyproject)
                if contract_env:
                    env_scope = contract_env
                    click.echo(
                        click.style("  scope    ", dim=True)
                        + f"contract declares env={contract_env} → features_{contract_env}"
                    )

            missing = _check_dependencies(
                workspace, product, env_name, feat_pyproject, short
            )
            if missing:
                click.echo()
                click.secho(
                    f"  {short} requires features not installed in {product}:",
                    fg="yellow",
                )
                for m in missing:
                    click.echo(f"    - {m}")
                click.echo()
                if not click.confirm("  Install them first?", default=True):
                    click.secho("  Aborted.", fg="red")
                    raise SystemExit(1)
                for m in missing:
                    click.echo()
                    subprocess.run(
                        [
                            "splent",
                            "feature:install",
                            f"{namespace_github}/splent_feature_{m}",
                        ],
                        text=True,
                    )

        # Add to product
        click.echo(click.style("  add      ", dim=True) + f"{short}")
        full_name = f"{namespace_github}/{feature_name}"
        cmd = ["splent", "feature:add", full_name]
        if env_scope:
            cmd.append(f"--{env_scope}")
        subprocess.run(cmd, capture_output=True, text=True)

    # ── Step 2: Env generate + merge ──────────────────────────────────
    # feature:add/attach already handled pyproject, symlink, and hot_reinstall.
    # We still need env merge for new variables (e.g. NGINX_UPSTREAM_HOST).
    click.echo(click.style("  env      ", dim=True) + "generate + merge")
    subprocess.run(
        ["splent", "product:env", "--generate", "--all", f"--{env_name}"],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["splent", "product:env", "--merge", f"--{env_name}"],
        capture_output=True,
        text=True,
    )

    # ── Step 3: Start feature Docker services (if any) ────────────────
    # hot_reinstall already reloaded Flask in the web container.
    # Here we only start the feature's own Docker services (nginx, redis, etc.)
    clean_ref = compose.normalize_feature_ref(
        f"{namespace_github}/{feature_name}" + (f"@{version}" if version else "")
    )
    docker_dir_f = compose.feature_docker_dir(workspace, clean_ref)
    base_path = os.path.dirname(docker_dir_f)
    compose_file = compose.resolve_file(base_path, env_name)

    if compose_file:
        proj = compose.project_name(f"{namespace_fs}/{feature_name}", env_name)
        click.echo(click.style("  docker   ", dim=True) + f"starting {short} services")
        result = subprocess.run(
            ["docker", "compose", "-p", proj, "-f", compose_file, "up", "-d"],
            cwd=docker_dir_f,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            click.secho(f"  Docker failed: {result.stderr.strip()}", fg="yellow")

    click.echo()
    click.secho(f"  {short} installed.", fg="green")
