import os
import subprocess
import click


def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _feature_cache_docker_dir(workspace: str, feature: str) -> str:
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


@click.command("product:entrypoint")
@click.option("--env", default="dev", help="Environment name (dev or prod)")
def product_entrypoint(env):
    """Ejecuta el entrypoint dentro del contenedor del producto."""
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    product_path = _get_product_path(product, workspace)
    docker_dir = os.path.join(product_path, "docker")
    entrypoint = os.path.join(product_path, "entrypoints", f"entrypoint.{env}.sh")
    compose_file = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback_file = os.path.join(docker_dir, "docker-compose.yml")

    compose_used = compose_file if os.path.exists(compose_file) else fallback_file
    project_name = _compose_project_name(product, env)

    result = subprocess.run(
        ["docker", "compose", "-p", project_name, "-f", compose_used, "ps", "-q"],
        cwd=docker_dir, capture_output=True, text=True
    )
    container_ids = [c.strip() for c in result.stdout.splitlines() if c.strip()]

    target_id = None
    for cid in container_ids:
        mounts = subprocess.run(
            ["docker", "inspect", "-f", "{{ range .Mounts }}{{ .Destination }} {{ end }}", cid],
            capture_output=True, text=True
        ).stdout.strip().split()
        if "/workspace" in mounts:
            target_id = cid
            break

    if not target_id and container_ids:
        target_id = container_ids[0]

    if target_id:
        click.echo(f"🧩 Executing entrypoint inside container {target_id[:12]}...")
        subprocess.run(["docker", "exec", "-i", target_id, "bash", "-lc", f"bash {entrypoint}"])
    else:
        click.echo("⚠️ No containers found, running locally")
        subprocess.run(["bash", entrypoint], cwd=docker_dir, check=False)
