"""
product:restart — Restart the active product's Flask app inside the container.
"""

import os
import subprocess

import click

from splent_cli.services import context, compose


@click.command(
    "product:restart",
    short_help="Restart the active product's Flask app.",
)
@click.option("--dev", "env_dev", is_flag=True, help="Restart development.")
@click.option("--prod", "env_prod", is_flag=True, help="Restart production.")
def product_restart(env_dev, env_prod):
    """Kill the running Flask/Gunicorn process and re-run the entrypoint."""
    product = context.require_app()
    workspace = str(context.workspace())
    env = context.resolve_env(env_dev, env_prod)
    product_path = os.path.join(workspace, product)

    compose_file = compose.resolve_file(product_path, env)
    if not compose_file:
        click.secho(f"❌ No docker-compose file found for {product} ({env})", fg="red")
        raise SystemExit(1)

    docker_dir = os.path.join(product_path, "docker")
    project_name = compose.project_name(product, env)

    container_id = compose.find_main_container(project_name, compose_file, docker_dir)
    if not container_id:
        click.secho(f"❌ No running container found for {product} ({env})", fg="red")
        raise SystemExit(1)

    click.echo(f"🔄 Restarting {product} ({env})...")

    # Kill existing Flask/watchmedo/gunicorn processes
    subprocess.run(
        ["docker", "exec", container_id, "bash", "-c",
         "pkill -f 'flask run' ; pkill -f watchmedo ; pkill -f gunicorn ; sleep 1"],
        capture_output=True,
    )

    # Re-run the entrypoint in background
    container_entrypoint = f"/workspace/{product}/entrypoints/entrypoint.{env}.sh"
    subprocess.run(
        ["docker", "exec", "-d", container_id, "bash", container_entrypoint],
        capture_output=True,
    )

    click.secho(f"✅ {product} restarted.", fg="green")


cli_command = product_restart
