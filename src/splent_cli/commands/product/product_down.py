import os
import subprocess
import click
import tomllib


def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _feature_cache_docker_dir(workspace: str, feature: str) -> str:
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


def _normalize_feature_ref(feat: str) -> str:
    """Ensure the feature ref follows org_safe/feature@version format."""
    if "features/" in feat:
        feat = feat.split("features/")[-1]
    if "/" not in feat:
        feat = f"splent_io/{feat}"
    return feat


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
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    product_path = _get_product_path(product, workspace)

    remove_volumes = False
    if v:
        confirm = input(
            "⚠️  This will remove ALL Docker volumes for the product and its features. Continue? (y/N): "
        )
        if confirm.lower() in ("y", "yes"):
            remove_volumes = True
            click.echo("🧹 Volumes will be removed.")
        else:
            click.echo("❎ Operation cancelled.")
            return

    def shutdown(name, docker_dir):
        compose_preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
        compose_fallback = os.path.join(docker_dir, "docker-compose.yml")
        compose_file = (
            compose_preferred if os.path.exists(compose_preferred) else compose_fallback
        )
        if not os.path.exists(compose_file):
            return
        project_name = _compose_project_name(name, env)

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

    for feat in features:
        clean = _normalize_feature_ref(feat)
        shutdown(clean, _feature_cache_docker_dir(workspace, clean))
