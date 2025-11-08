import os
import tomllib
import click


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


def _feature_cache_docker_dir(workspace: str, feature: str) -> str:
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


@click.command("product:merge-envs")
def product_merge_envs():
    """Fusiona los .env de las features en el del producto."""
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    product_path = _get_product_path(product, workspace)
    product_env = os.path.join(product_path, "docker", ".env")

    if not os.path.exists(product_env):
        click.echo(f"❌ Product .env not found at {product_env}")
        return

    py = os.path.join(product_path, "pyproject.toml")
    with open(py, "rb") as f:
        data = tomllib.load(f)
    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    feature_envs = [os.path.join(_feature_cache_docker_dir(workspace, f), ".env") for f in features]

    env_dict = {}
    with open(product_env, "r") as f:
        for line in f:
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1)
                env_dict[k] = v
    for f_env in feature_envs:
        if os.path.exists(f_env):
            with open(f_env, "r") as f:
                for line in f:
                    if "=" in line and not line.startswith("#"):
                        k, v = line.strip().split("=", 1)
                        env_dict.setdefault(k, v)
    with open(product_env, "w") as f:
        for k, v in env_dict.items():
            f.write(f"{k}={v}\n")

    click.echo("🔗 Merged feature .env values into product .env")
