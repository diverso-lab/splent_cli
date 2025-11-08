import os
import subprocess
import tomllib
import click


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


def _feature_cache_docker_dir(workspace: str, feature: str) -> str:
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")

@click.command("product:generate-envs")
@click.option("--env", default="dev", help="Environment name (dev or prod)")
def product_generate_envs(env):
    """Genera los archivos .env para el producto y sus features."""
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    product_path = _get_product_path(product, workspace)

    py = os.path.join(product_path, "pyproject.toml")
    with open(py, "rb") as f:
        data = tomllib.load(f)
    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])

    docker_dirs = [(product, os.path.join(product_path, "docker"))] + [
        (feat, _feature_cache_docker_dir(workspace, feat)) for feat in features
    ]

    for name, docker_dir in docker_dirs:
        if not os.path.exists(docker_dir):
            continue
        env_file = os.path.join(docker_dir, ".env")
        if os.path.exists(env_file):
            click.echo(f"ℹ️  Using existing {name}/docker/.env")
            continue
        example = os.path.join(docker_dir, f".env.{env}.example")
        fallback = os.path.join(docker_dir, ".env.example")
        selected = example if os.path.exists(example) else fallback if os.path.exists(fallback) else None
        if selected:
            subprocess.run(["cp", selected, env_file], check=True)
            click.echo(f"📄 Created {name}/docker/.env from {os.path.basename(selected)}")
        else:
            click.echo(f"⚠️  No .env template found for {name} in {docker_dir}")