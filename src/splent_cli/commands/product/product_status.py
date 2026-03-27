import os
import subprocess
import json
import tomllib

import click
from splent_cli.services import compose, context
from splent_cli.utils.feature_utils import read_features_from_data


def _status_color(state: str) -> str:
    state_l = state.lower()
    if "running" in state_l:
        return click.style(state, fg="green")
    if "exited" in state_l or "stopped" in state_l:
        return click.style(state, fg="red")
    if "starting" in state_l or "restarting" in state_l:
        return click.style(state, fg="yellow")
    if "unhealthy" in state_l:
        return click.style(state, fg="red")
    if "healthy" in state_l:
        return click.style(state, fg="green")
    return state


def _get_containers(project_name: str, compose_file: str) -> list[dict]:
    """Run docker compose ps and return parsed container list."""
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
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    containers = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return containers


def _format_ports(ports) -> str:
    if isinstance(ports, list):
        return ", ".join(
            f"{p.get('PublishedPort', '')}→{p.get('TargetPort', '')}"
            for p in ports
            if p.get("PublishedPort")
        )
    return str(ports) if ports else "—"


@click.command(
    "product:status",
    short_help="Show Docker container status for the active product and its features.",
)
@click.option("--dev", "env_dev", is_flag=True, help="Use development environment.")
@click.option("--prod", "env_prod", is_flag=True, help="Use production environment.")
def product_status(env_dev, env_prod):
    """Shows the status of all Docker containers for the active product and its features."""
    if env_dev and env_prod:
        click.secho("❌ Cannot specify both --dev and --prod.", fg="red")
        raise SystemExit(1)

    env = context.resolve_env(env_dev, env_prod)
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)

    all_containers: list[tuple[str, dict]] = []  # (source_label, container)

    # Feature containers
    pyproject_path = os.path.join(product_path, "pyproject.toml")
    if os.path.exists(pyproject_path):
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        features = read_features_from_data(data, env)

        for feat in features:
            clean = compose.normalize_feature_ref(feat)
            docker_dir = compose.feature_docker_dir(workspace, clean)
            compose_preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
            compose_fallback = os.path.join(docker_dir, "docker-compose.yml")
            compose_file = (
                compose_preferred
                if os.path.exists(compose_preferred)
                else compose_fallback
            )

            if not os.path.exists(compose_file):
                continue

            proj = compose.project_name(clean, env)
            for c in _get_containers(proj, compose_file):
                # Extract short feature name for label
                short = clean.split("/")[-1] if "/" in clean else clean
                all_containers.append((short, c))

    # Product containers
    product_compose = compose.resolve_file(product_path, env)
    if product_compose:
        proj = compose.project_name(product, env)
        for c in _get_containers(proj, product_compose):
            all_containers.append((product, c))

    if not all_containers:
        click.secho(
            f"ℹ️  No containers found for {product} ({env}). Is it up?", fg="yellow"
        )
        return

    click.secho(f"\n{product}  [{env}]\n", bold=True)

    col_source = max(len(src) for src, _ in all_containers) + 2
    col_source = max(col_source, len("SOURCE") + 2)
    col_name = 30
    col_state = 14

    header = f"  {'SOURCE':<{col_source}} {'SERVICE':<{col_name}} {'STATUS':<{col_state}} PORTS"
    click.secho(header, fg="cyan")
    click.echo("  " + "─" * (col_source + col_name + col_state + 20))

    for source, c in all_containers:
        name = c.get("Service") or c.get("Name", "?")
        state = c.get("State") or c.get("Status", "?")
        ports = _format_ports(c.get("Publishers") or c.get("Ports", ""))

        source_styled = click.style(f"{source:<{col_source}}", fg="bright_black")
        click.echo(
            f"  {source_styled} {name:<{col_name}} {_status_color(state):<{col_state}} {ports}"
        )

    click.echo()


cli_command = product_status
