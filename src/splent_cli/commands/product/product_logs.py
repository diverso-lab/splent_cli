import subprocess
import click
from splent_cli.services import compose, context


@click.command("product:logs", short_help="Tail logs of the active product's containers.")
@click.option("--dev", "env_dev", is_flag=True, help="Use development environment.")
@click.option("--prod", "env_prod", is_flag=True, help="Use production environment.")
@click.option("--tail", default=50, show_default=True, help="Number of lines to show from the end.")
@click.option("--no-follow", is_flag=True, help="Print logs and exit without following.")
@click.option("--service", default=None, help="Show logs for a specific service only.")
def product_logs(env_dev, env_prod, tail, no_follow, service):
    """
    Stream logs from the active product's Docker Compose services.

    Follows output by default (Ctrl+C to stop). Use --no-follow to print
    a snapshot and exit.
    """
    if env_dev and env_prod:
        click.secho("❌ Cannot specify both --dev and --prod.", fg="red")
        raise SystemExit(1)

    env = context.resolve_env(env_dev, env_prod)
    product = context.require_app()
    product_path = str(context.workspace() / product)
    compose_file = compose.resolve_file(product_path, env)

    if not compose_file:
        click.secho(f"❌ No docker-compose file found for {product} ({env}).", fg="red")
        raise SystemExit(1)

    project_name = compose.project_name(product, env)
    cmd = [
        "docker", "compose",
        "-p", project_name,
        "-f", compose_file,
        "logs",
        "--tail", str(tail),
    ]
    if not no_follow:
        cmd.append("-f")
    if service:
        cmd.append(service)

    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        pass


cli_command = product_logs
