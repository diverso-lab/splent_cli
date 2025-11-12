import os
import subprocess
import click
import tomllib


# === HELPERS ====================================================


def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _feature_cache_docker_dir(workspace: str, feature: str) -> str:
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


def _normalize_feature_ref(feat: str) -> str:
    """Ensure the feature reference follows org_safe/feature@version format."""
    if "features/" in feat:
        feat = feat.split("features/")[-1]
    if "/" not in feat:
        feat = f"splent_io/{feat}"
    return feat


@click.command("product:up")
@click.option("--dev", is_flag=True, help="Run in development mode.")
@click.option("--prod", is_flag=True, help="Run in production mode.")
def product_up(dev, prod):
    """Starts the product and its features using Docker Compose."""
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")

    # Determine environment
    if dev and prod:
        click.echo("❌ You cannot specify both --dev and --prod.")
        raise SystemExit(1)
    elif dev:
        env = "dev"
    elif prod:
        env = "prod"
    else:
        env = os.getenv("SPLENT_ENV")
        if not env:
            click.echo(
                "❌ No environment specified. Use --dev, --prod or set SPLENT_ENV."
            )
            raise SystemExit(1)

    product_path = _get_product_path(product, workspace)

    def launch(name, docker_dir):
        compose_preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
        compose_fallback = os.path.join(docker_dir, "docker-compose.yml")
        compose_file = (
            compose_preferred if os.path.exists(compose_preferred) else compose_fallback
        )
        if not os.path.exists(compose_file):
            click.echo(f"⚠️ No docker-compose file for {name}")
            return
        project_name = _compose_project_name(name, env)
        subprocess.run(
            ["docker", "compose", "-p", project_name, "-f", compose_file, "up", "-d"],
            check=False,
        )
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
    for feat in features:
        clean = _normalize_feature_ref(feat)
        launch(clean, _feature_cache_docker_dir(workspace, clean))

    # Launch product last
    launch(product, os.path.join(product_path, "docker"))
