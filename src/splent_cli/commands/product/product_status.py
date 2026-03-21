import os
import subprocess
import json
import click
import tomllib


def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _resolve_compose_file(product_path: str, env: str) -> str | None:
    docker_dir = os.path.join(product_path, "docker")
    preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback = os.path.join(docker_dir, "docker-compose.yml")
    if os.path.exists(preferred):
        return preferred
    if os.path.exists(fallback):
        return fallback
    return None


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


@click.command("product:status", short_help="Show Docker container status for the active product.")
@click.option("--dev", "env_dev", is_flag=True, help="Use development environment.")
@click.option("--prod", "env_prod", is_flag=True, help="Use production environment.")
def product_status(env_dev, env_prod):
    """Shows the status of all Docker containers for the active product."""
    if env_dev and env_prod:
        click.secho("❌ Cannot specify both --dev and --prod.", fg="red")
        raise SystemExit(1)

    env = "prod" if env_prod else "dev" if env_dev else os.getenv("SPLENT_ENV", "dev")
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")

    if not product:
        click.secho("❌ SPLENT_APP not set.", fg="red")
        raise SystemExit(1)

    product_path = os.path.join(workspace, product)
    compose_file = _resolve_compose_file(product_path, env)

    if not compose_file:
        click.secho(f"❌ No docker-compose file found for {product} ({env}).", fg="red")
        raise SystemExit(1)

    project_name = _compose_project_name(product, env)

    result = subprocess.run(
        [
            "docker", "compose",
            "-p", project_name,
            "-f", compose_file,
            "ps",
            "--format", "json",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        click.secho(f"❌ docker compose ps failed:\n{result.stderr.strip()}", fg="red")
        raise SystemExit(1)

    raw = result.stdout.strip()
    if not raw:
        click.secho(f"ℹ️  No containers found for {product} ({env}). Is it up?", fg="yellow")
        return

    # docker compose ps --format json outputs one JSON object per line
    containers = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            containers.append(obj)
        except json.JSONDecodeError:
            continue

    if not containers:
        click.secho(f"ℹ️  No containers found for {product} ({env}).", fg="yellow")
        return

    click.secho(f"\n{product}  [{env}]\n", bold=True)
    col_name = 36
    col_state = 24
    col_ports = 0

    header = f"  {'SERVICE':<{col_name}} {'STATUS':<{col_state}} PORTS"
    click.secho(header, fg="cyan")
    click.echo("  " + "─" * 72)

    for c in containers:
        name = c.get("Service") or c.get("Name", "?")
        state = c.get("State") or c.get("Status", "?")
        ports = c.get("Publishers") or c.get("Ports", "")
        if isinstance(ports, list):
            ports = ", ".join(
                f"{p.get('PublishedPort', '')}→{p.get('TargetPort', '')}"
                for p in ports if p.get("PublishedPort")
            )
        click.echo(f"  {name:<{col_name}} {_status_color(state):<{col_state}} {ports or '—'}")

    click.echo()


cli_command = product_status
