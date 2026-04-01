"""
product:restart — Restart the active product's Flask app inside the container.
"""

import os
import subprocess

import click

from splent_cli.services import context, compose


def _load_env_into_container(container_id: str, env_file: str):
    """Export all vars from the .env file into the running container's shell env.

    Docker env_file is only applied at container creation. This injects
    updated vars so that processes started inside the container (like Flask)
    see them via os.getenv().
    """
    if not os.path.isfile(env_file):
        return

    exports = []
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                exports.append(f"export {line}")

    if exports:
        cmd = " && ".join(exports) + " && env > /tmp/.splent_env_reload"
        subprocess.run(
            ["docker", "exec", container_id, "bash", "-c", cmd],
            capture_output=True,
        )


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

    By default, kills Flask/watchmedo processes and restarts them with
    the current .env vars loaded. Use --full to re-run the entire
    entrypoint (reinstall deps, migrations, etc.).
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

    env_file = os.path.join(product_path, "docker", ".env")

    # Kill existing Flask/watchmedo/gunicorn processes
    subprocess.run(
        [
            "docker", "exec", container_id, "bash", "-c",
            "pkill -f 'flask run' ; pkill -f watchmedo ; pkill -f gunicorn ; sleep 1",
        ],
        capture_output=True,
    )

    if full:
        click.echo(click.style("  restarting ", dim=True) + f"{product} ({env}) — full entrypoint")

        # Source .env and run the full entrypoint
        container_entrypoint = f"/workspace/{product}/entrypoints/entrypoint.{env}.sh"
        source_cmd = f"set -a && . /workspace/{product}/docker/.env && set +a && bash {container_entrypoint}"
        subprocess.run(
            ["docker", "exec", "-d", container_id, "bash", "-c", source_cmd],
            capture_output=True,
        )
    else:
        click.echo(click.style("  restarting ", dim=True) + f"{product} ({env})")

        # Source .env and restart Flask via the start script
        start_script = f"/workspace/{product}/scripts/05_0_start_app_{env}.sh"
        source_cmd = f"set -a && . /workspace/{product}/docker/.env && set +a && bash {start_script}"
        subprocess.run(
            ["docker", "exec", "-d", container_id, "bash", "-c", source_cmd],
            capture_output=True,
        )

    click.secho("  done.", fg="green")


cli_command = product_restart
