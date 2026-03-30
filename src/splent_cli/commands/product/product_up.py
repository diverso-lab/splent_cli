import os
import subprocess
import click
import tomllib
from splent_cli.services import compose, context
from splent_cli.utils.feature_utils import read_features_from_data


def _check_docker_running():
    """Exit with a clear message if the Docker daemon is not reachable."""
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        click.secho(
            "❌ Docker daemon is not running or not reachable.\n"
            "   Start Docker and try again.",
            fg="red",
        )
        raise SystemExit(1)


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

    _check_docker_running()

    env = context.resolve_env(dev, prod)
    product = context.require_app()
    product_path = compose.product_path(product, str(context.workspace()))

    failed: list[str] = []

    def launch(name, base_path):
        compose_file = compose.resolve_file(base_path, env)
        if compose_file is None:
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
            failed.append(name)
        else:
            click.echo(f"✅  {name}: started successfully")

    # Load features
    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py):
        click.echo("❌ pyproject.toml not found in product path.")
        raise SystemExit(1)

    with open(py, "rb") as f:
        data = tomllib.load(f)

    features = read_features_from_data(data, env)
    ws = str(context.workspace())
    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        feat_docker = compose.feature_docker_dir(ws, clean)
        launch(clean, os.path.dirname(feat_docker))

    if failed:
        click.secho(
            f"\n❌ {len(failed)} service(s) failed to start: {', '.join(failed)}\n"
            "   The product was not launched. Fix the failing services and retry.",
            fg="red",
        )
        raise SystemExit(1)

    # Launch product last — only if all feature services started successfully
    launch(product, product_path)

    if failed:
        raise SystemExit(1)
