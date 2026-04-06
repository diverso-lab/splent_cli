import os
import subprocess
import click
import tomllib
from splent_cli.services import compose, context
from splent_cli.utils.feature_utils import read_features_from_data


@click.command(
    "product:down",
    short_help="Stop the product and its features (optionally removing volumes).",
)
@click.option("--dev", is_flag=True, help="Stop development environment.")
@click.option("--prod", is_flag=True, help="Stop production deployment.")
@click.option("--v", is_flag=True, help="Remove all volumes (requires confirmation).")
def product_down(dev, prod, v):
    """Stop the product and its features using Docker Compose.

    \b
    --dev   stops the development containers (features + product).
    --prod  stops the production deployment (docker-compose.deploy.yml).

    If neither flag is given, stops whichever is currently running.
    """
    if dev and prod:
        click.secho("❌ You cannot specify both --dev and --prod.", fg="red")
        raise SystemExit(1)

    product = context.require_app()
    workspace = str(context.workspace())
    product_path = compose.product_path(product, workspace)
    docker_dir = os.path.join(product_path, "docker")

    # Auto-detect if no flag given
    if not dev and not prod:
        deploy_compose = os.path.join(docker_dir, "docker-compose.deploy.yml")
        if os.path.isfile(deploy_compose):
            result = subprocess.run(
                ["docker", "compose", "-f", deploy_compose, "ps", "-q"],
                capture_output=True,
                text=True,
                cwd=docker_dir,
            )
            if result.stdout.strip():
                prod = True

        if not prod:
            dev = True

    # Volume removal confirmation
    remove_volumes = False
    if v:
        if not click.confirm(
            "⚠️  This will remove ALL Docker volumes. Continue?",
            default=False,
        ):
            click.echo("❎ Operation cancelled.")
            return
        remove_volumes = True
        click.echo("🧹 Volumes will be removed.")

    # --prod: stop deployment
    if prod:
        deploy_compose = os.path.join(docker_dir, "docker-compose.deploy.yml")
        if not os.path.isfile(deploy_compose):
            click.secho("⚠️  No docker-compose.deploy.yml found.", fg="yellow")
            return

        args = ["docker", "compose", "-f", deploy_compose, "down"]
        if remove_volumes:
            args += ["-v"]
        subprocess.run(args, check=False, cwd=docker_dir)
        click.secho("🛑 Production deployment stopped.", fg="green")
        return

    # --dev: stop development containers
    env = "dev"

    def shutdown(name, base_path):
        compose_file = compose.resolve_file(base_path, env)
        if compose_file is None:
            return
        project_name = compose.project_name(name, env)

        args = ["docker", "compose", "-p", project_name, "-f", compose_file, "down"]
        if remove_volumes:
            args += ["-v"]
        subprocess.run(args, check=False)
        click.echo(f"🛑  {name}: stopped successfully")

    shutdown(product, product_path)

    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py):
        click.echo("⚠️ pyproject.toml not found.")
        return

    with open(py, "rb") as f:
        data = tomllib.load(f)
    features = read_features_from_data(data, env)

    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        feat_docker = compose.feature_docker_dir(workspace, clean)
        shutdown(clean, os.path.dirname(feat_docker))

    click.secho("🛑 Development environment stopped.", fg="green")


cli_command = product_down
