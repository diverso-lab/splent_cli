import os
import subprocess
import time

import click
import tomllib
import yaml

from splent_cli.services import compose, context
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.utils.proc import require_docker


def _check_docker_running() -> None:
    """Abort unless Docker is installed and its daemon is reachable.

    Thin wrapper around :func:`splent_cli.utils.proc.require_docker` kept as a
    named, individually-testable unit for the "is docker running" guarantee that
    ``product:up`` depends on. Delegating to the shared helper means the
    not-installed / daemon-down / permission-denied messages stay consistent
    across the CLI (and distinct from one another).
    """
    require_docker()


def _sync_splent_env(product_path: str, env: str) -> None:
    """Write ``SPLENT_ENV=<env>`` into the product's ``docker/.env``.

    Keeps the persisted environment in sync with the ``--dev``/``--prod`` flag so a
    product that was previously deployed (which sets ``SPLENT_ENV=prod``) can return
    to development with ``product:up --dev`` / ``product:derive --dev``. The container
    is (re)created right after this, so it picks up the updated value.
    """
    env_path = os.path.join(product_path, "docker", ".env")
    if not os.path.isfile(env_path):
        return
    lines = []
    found = False
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith("SPLENT_ENV="):
                lines.append(f"SPLENT_ENV={env}\n")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"SPLENT_ENV={env}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _get_feature_order(workspace, product_path, env):
    """Return features in UVL topological order."""
    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.isfile(py):
        click.secho("  pyproject.toml not found in product path.", fg="red")
        raise SystemExit(1)

    with open(py, "rb") as f:
        data = tomllib.load(f)

    features = read_features_from_data(data, env)

    # Try to order via UVL
    try:
        from splent_framework.managers.feature_order import FeatureLoadOrderResolver
    except ImportError:
        # Framework not available: fall back to declared feature order silently.
        return features

    spl_name = data.get("tool", {}).get("splent", {}).get("spl")
    if spl_name:
        uvl = os.path.join(workspace, "splent_catalog", spl_name, f"{spl_name}.uvl")
        if os.path.isfile(uvl):
            try:
                return FeatureLoadOrderResolver().resolve(features, uvl)
            except Exception as e:
                click.secho(
                    f"  Could not resolve UVL load order from {uvl}:\n"
                    f"    {e}\n"
                    "  Falling back to unordered feature launch.",
                    fg="yellow",
                )

    return features


def _has_healthcheck(docker_dir, env):
    """Check if a feature's compose file declares a healthcheck on any service."""
    compose_file = compose.resolve_file(os.path.dirname(docker_dir), env)
    if not compose_file:
        return False

    try:
        with open(compose_file) as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        click.secho(
            f"  Could not parse compose file {compose_file}:\n"
            f"    {e}\n"
            "  Skipping health-wait for this feature.",
            fg="yellow",
        )
        return False
    for svc_def in data.get("services", {}).values():
        if "healthcheck" in svc_def:
            return True
    return False


def _has_build(docker_dir, env):
    """Check if a feature's compose file uses build: directive."""
    compose_file = compose.resolve_file(os.path.dirname(docker_dir), env)
    if not compose_file:
        return False

    try:
        with open(compose_file) as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        click.secho(
            f"  Could not parse compose file {compose_file}:\n"
            f"    {e}\n"
            "  Assuming no build directive for this feature.",
            fg="yellow",
        )
        return False
    for svc_def in data.get("services", {}).values():
        if "build" in svc_def:
            return True
    return False


def _get_service_names(docker_dir, env):
    """Return list of service names from a feature's compose file."""
    compose_file = compose.resolve_file(os.path.dirname(docker_dir), env)
    if not compose_file:
        return []

    try:
        with open(compose_file) as f:
            data = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as e:
        click.secho(
            f"  Could not parse compose file {compose_file}:\n    {e}",
            fg="yellow",
        )
        return []
    return list(data.get("services", {}).keys())


def _wait_for_healthy(project_name, compose_file, docker_dir, timeout=60):
    """Wait for all services in a compose project to be healthy.

    Only waits for services that declare a healthcheck. Services without
    healthcheck are considered ready immediately.
    """
    deadline = time.time() + timeout

    while time.time() < deadline:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                project_name,
                "-f",
                compose_file,
                "ps",
                "--format",
                "json",
            ],
            cwd=docker_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            time.sleep(2)
            continue

        import json

        all_healthy = True
        for line in result.stdout.strip().splitlines():
            try:
                container = json.loads(line)
                health = container.get("Health", "")
                if health and health != "healthy":
                    all_healthy = False
                    break
            except json.JSONDecodeError:
                continue

        if all_healthy:
            return True

        time.sleep(2)

    return False


@click.command(
    "product:up",
    short_help="Start the active product and all its features with Docker Compose.",
)
@click.option("--dev", is_flag=True, help="Run in development mode.")
@click.option("--prod", is_flag=True, help="Run in production mode.")
def product_up(dev, prod):
    """Start the product and its features using Docker Compose.

    \b
    Features are launched in UVL dependency order. If a feature declares
    a Docker healthcheck, the next feature waits until it is healthy.
    """
    if dev and prod:
        click.secho("  You cannot specify both --dev and --prod.", fg="red")
        raise SystemExit(1)

    if not dev and not prod and not os.getenv("SPLENT_ENV"):
        click.secho(
            "  No environment specified. Use --dev, --prod or set SPLENT_ENV.",
            fg="red",
        )
        raise SystemExit(1)

    _check_docker_running()

    env = context.resolve_env(dev, prod)
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = compose.product_path(product, workspace)

    # Persist the resolved environment so a previously deployed (prod) product can
    # cleanly return to dev — the container is recreated below with the new value.
    _sync_splent_env(product_path, env)

    failed: list[str] = []
    started: list[str] = []

    def launch(name, base_path, wait_health=False):
        compose_file = compose.resolve_file(base_path, env)
        if compose_file is None:
            return True

        project = compose.project_name(name, env)
        docker_dir = os.path.join(base_path, "docker")

        # Build if needed (dev mode — compose handles it natively with up --build)
        needs_build = False
        feat_docker = os.path.join(base_path, "docker")
        if os.path.isdir(feat_docker) and _has_build(feat_docker, env):
            needs_build = True

        cmd = ["docker", "compose", "-p", project, "-f", compose_file, "up", "-d"]
        if needs_build:
            cmd.append("--build")

        short = name.split("/")[-1] if "/" in name else name

        # Show progress: service name while launching
        click.echo(
            click.style("    ⏳ ", dim=True) + click.style(short, dim=True),
            nl=False,
        )

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            click.echo(click.style(" ✗", fg="red"))
            if result.stderr.strip():
                for line in result.stderr.strip().splitlines():
                    click.echo(f"       {line}")
            failed.append(name)
            return False

        started.append(short)

        # Wait for health if this feature has healthchecks
        if wait_health:
            click.echo(click.style(" waiting...", dim=True), nl=False)
            healthy = _wait_for_healthy(project, compose_file, docker_dir)
            if healthy:
                click.echo(click.style(" ✓", fg="green"))
            else:
                click.echo(click.style(" timeout", fg="yellow"))
        else:
            click.echo(click.style(" ✓", fg="green"))

        return True

    # Get features in UVL dependency order
    features = _get_feature_order(workspace, product_path, env)

    # Launch features in order
    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        feat_docker = compose.feature_docker_dir(workspace, clean)
        feat_base = os.path.dirname(feat_docker)

        if not os.path.isdir(feat_docker):
            continue

        has_hc = _has_healthcheck(feat_docker, env)
        launch(clean, feat_base, wait_health=has_hc)

    if failed:
        click.secho(
            f"\n  {len(failed)} service(s) failed to start: {', '.join(failed)}\n"
            "  The product was not launched. Fix the failing services and retry.",
            fg="red",
        )
        raise SystemExit(1)

    # Launch product last
    launch(product, product_path)

    if failed:
        raise SystemExit(1)


cli_command = product_up
