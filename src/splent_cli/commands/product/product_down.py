import os
import subprocess
import click
import tomllib
from splent_cli.services import compose, context


@click.command(
    "product:down",
    short_help="Stop the product and its features (optionally removing volumes)."
)
@click.option("--env", default="dev", help="Environment name (dev or prod)")
@click.option("--v", is_flag=True, help="Remove all volumes (requires confirmation)")
def product_down(env, v):
    """Stops the product and its features using Docker Compose.
    Use --v to also remove all volumes.
    """
    product = context.require_app()
    product_path = compose.product_path(product, str(context.workspace()))

    remove_volumes = False
    if v:
        if not click.confirm("⚠️  This will remove ALL Docker volumes for the product and its features. Continue?", default=False):
            click.echo("❎ Operation cancelled.")
            return
        remove_volumes = True
        click.echo("🧹 Volumes will be removed.")

    def shutdown(name, docker_dir):
        compose_preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
        compose_fallback = os.path.join(docker_dir, "docker-compose.yml")
        compose_file = (
            compose_preferred if os.path.exists(compose_preferred) else compose_fallback
        )
        if not os.path.exists(compose_file):
            return
        project_name = compose.project_name(name, env)

        args = ["docker", "compose", "-p", project_name, "-f", compose_file, "down"]
        if remove_volumes:
            args += ["-v"]
        subprocess.run(args, check=False)
        click.echo(f"🛑  {name}: stopped successfully")

    # Stop the product first (to avoid dependency issues)
    shutdown(product, os.path.join(product_path, "docker"))

    # Then stop the features
    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py):
        click.echo("⚠️ pyproject.toml not found.")
        return

    with open(py, "rb") as f:
        data = tomllib.load(f)
    features = (
        data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    )

    ws = str(context.workspace())
    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        shutdown(clean, compose.feature_docker_dir(ws, clean))
