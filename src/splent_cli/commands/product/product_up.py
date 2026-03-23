import os
import subprocess
import click
import tomllib
from splent_cli.services import compose, context


@click.command(
    "product:up",
    short_help="Start the active product and all its features with Docker Compose.",
)
@click.option("--dev", is_flag=True, help="Run in development mode.")
@click.option("--prod", is_flag=True, help="Run in production mode.")
def product_up(dev, prod):
    """Starts the product and its features using Docker Compose."""
    if dev and prod:
        click.echo("❌ You cannot specify both --dev and --prod.")
        raise SystemExit(1)

    if not dev and not prod and not os.getenv("SPLENT_ENV"):
        click.echo("❌ No environment specified. Use --dev, --prod or set SPLENT_ENV.")
        raise SystemExit(1)

    env = context.resolve_env(dev, prod)
    product = context.require_app()
    product_path = compose.product_path(product, str(context.workspace()))

    def launch(name, docker_dir):
        compose_preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
        compose_fallback = os.path.join(docker_dir, "docker-compose.yml")
        compose_file = (
            compose_preferred if os.path.exists(compose_preferred) else compose_fallback
        )
        if not os.path.exists(compose_file):
            click.echo(f"⚠️ No docker-compose file for {name}")
            return
        project_name = compose.project_name(name, env)
        result = subprocess.run(
            ["docker", "compose", "-p", project_name, "-f", compose_file, "up", "-d"],
            check=False,
        )
        if result.returncode != 0:
            click.secho(
                f"❌  {name}: failed to start (exit {result.returncode})", fg="red"
            )
        else:
            click.echo(f"✅  {name}: started successfully")

    # Load features
    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py):
        click.echo("❌ pyproject.toml not found in product path.")
        raise SystemExit(1)

    with open(py, "rb") as f:
        data = tomllib.load(f)

    features = (
        data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    )
    ws = str(context.workspace())
    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        launch(clean, compose.feature_docker_dir(ws, clean))

    # Launch product last
    launch(product, os.path.join(product_path, "docker"))
