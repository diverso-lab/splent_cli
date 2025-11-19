import os
import subprocess
import click


def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


@click.command(
    "product:run",
    short_help="Run the product entrypoint inside its main container."
)
@click.option(
    "--dev",
    "env_dev",
    is_flag=True,
    help="Run using the development environment."
)
@click.option(
    "--prod",
    "env_prod",
    is_flag=True,
    help="Run using the production environment."
)
def product_runc(env_dev, env_prod):
    """
    Execute the product entrypoint inside the main container.
    Only one of --dev or --prod is allowed.
    Default environment: dev
    """

    # -------------------------------------------------------------
    # 1. MUTUAL EXCLUSION CHECK
    # -------------------------------------------------------------
    if env_dev and env_prod:
        click.secho("❌ You cannot use --dev and --prod at the same time.", fg="red")
        raise SystemExit(1)

    # -------------------------------------------------------------
    # 2. DETERMINE ENVIRONMENT
    # -------------------------------------------------------------
    if env_prod:
        env = "prod"
    else:
        env = "dev"

    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")

    if not product:
        click.secho("❌ SPLENT_APP not defined.", fg="red")
        raise SystemExit(1)

    product_path = _get_product_path(product, workspace)
    docker_dir = os.path.join(product_path, "docker")

    entrypoint = os.path.join(product_path, "entrypoints", f"entrypoint.{env}.sh")
    compose_file = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback_file = os.path.join(docker_dir, "docker-compose.yml")

    compose_used = compose_file if os.path.exists(compose_file) else fallback_file
    project_name = _compose_project_name(product, env)

    # -------------------------------------------------------------
    # 3. FIND CONTAINER
    # -------------------------------------------------------------
    result = subprocess.run(
        ["docker", "compose", "-p", project_name, "-f", compose_used, "ps", "-q"],
        cwd=docker_dir,
        capture_output=True,
        text=True,
    )
    container_ids = [c.strip() for c in result.stdout.splitlines() if c.strip()]

    target_id = None
    for cid in container_ids:
        mounts = (
            subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{ range .Mounts }}{{ .Destination }} {{ end }}",
                    cid,
                ],
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .split()
        )
        if "/workspace" in mounts:
            target_id = cid
            break

    if not target_id and container_ids:
        target_id = container_ids[0]

    # -------------------------------------------------------------
    # 4. EXEC ENTRYPOINT
    # -------------------------------------------------------------
    if target_id:
        click.echo(f"🧩 Executing entrypoint ({env}) in container {target_id[:12]}...")
        subprocess.run(
            ["docker", "exec", "-i", target_id, "bash", "-lc", f"bash {entrypoint}"]
        )
    else:
        click.echo(f"⚠️ No containers found, running locally ({env})")
        subprocess.run(["bash", entrypoint], cwd=docker_dir, check=False)
