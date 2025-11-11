import os
import subprocess
import click
import tomllib
from collections import OrderedDict


# === HELPERS ====================================================

def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _feature_cache_docker_dir(workspace: str, feature: str) -> str:
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


def _normalize_feature_ref(feat: str) -> str:
    """Garantiza que el feature esté en formato org_safe/feature@version."""
    if "features/" in feat:
        feat = feat.split("features/")[-1]
    if "/" not in feat:
        feat = f"splent_io/{feat}"
    return feat


@click.command("product:up")
@click.option("--env", default="dev", help="Environment name (dev or prod)")
def product_up(env):
    """Levanta el producto y sus features en Docker Compose."""
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    product_path = _get_product_path(product, workspace)

    def launch(name, docker_dir):
        compose_preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
        compose_fallback = os.path.join(docker_dir, "docker-compose.yml")
        compose_file = compose_preferred if os.path.exists(compose_preferred) else compose_fallback
        if not os.path.exists(compose_file):
            click.echo(f"⚠️ No docker-compose file for {name}")
            return
        project_name = _compose_project_name(name, env)
        subprocess.run(["docker", "compose", "-p", project_name, "-f", compose_file, "up", "-d"], check=False)
        click.echo(f"✅  {name}: started successfully")

    # Features
    py = os.path.join(product_path, "pyproject.toml")
    with open(py, "rb") as f:
        data = tomllib.load(f)
    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    for feat in features:
        clean = _normalize_feature_ref(feat)
        launch(clean, _feature_cache_docker_dir(workspace, clean))

    # Producto
    launch(product, os.path.join(product_path, "docker"))
