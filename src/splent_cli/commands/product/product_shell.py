import os
import subprocess
import click


def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _resolve_compose_file(product_path: str, env: str) -> str | None:
    docker_dir = os.path.join(product_path, "docker")
    preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback = os.path.join(docker_dir, "docker-compose.yml")
    if os.path.exists(preferred):
        return preferred
    if os.path.exists(fallback):
        return fallback
    return None


def _find_main_container(project_name: str, compose_file: str, docker_dir: str) -> str | None:
    """Find the main container — the one with /workspace mounted."""
    result = subprocess.run(
        ["docker", "compose", "-p", project_name, "-f", compose_file, "ps", "-q"],
        cwd=docker_dir,
        capture_output=True,
        text=True,
    )
    container_ids = [c.strip() for c in result.stdout.splitlines() if c.strip()]

    for cid in container_ids:
        mounts = subprocess.run(
            ["docker", "inspect", "-f", "{{ range .Mounts }}{{ .Destination }} {{ end }}", cid],
            capture_output=True,
            text=True,
        ).stdout.strip().split()
        if "/workspace" in mounts:
            return cid

    return container_ids[0] if container_ids else None


@click.command("product:shell", short_help="Open a shell inside the active product's container.")
@click.option("--dev", "env_dev", is_flag=True, help="Use development environment.")
@click.option("--prod", "env_prod", is_flag=True, help="Use production environment.")
@click.option("--service", default=None, help="Target a specific service by name.")
@click.option("--cmd", default=None, help="Run a specific command instead of an interactive shell.")
def product_shell(env_dev, env_prod, service, cmd):
    """
    Open an interactive shell (bash/sh) inside the active product's main container.

    The main container is identified as the one with /workspace mounted.
    Use --service to target a different container by service name.
    Use --cmd to run a one-off command instead of an interactive shell.
    """
    if env_dev and env_prod:
        click.secho("❌ Cannot specify both --dev and --prod.", fg="red")
        raise SystemExit(1)

    env = "prod" if env_prod else "dev" if env_dev else os.getenv("SPLENT_ENV", "dev")
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")

    if not product:
        click.secho("❌ SPLENT_APP not set.", fg="red")
        raise SystemExit(1)

    product_path = os.path.join(workspace, product)
    docker_dir = os.path.join(product_path, "docker")
    compose_file = _resolve_compose_file(product_path, env)

    if not compose_file:
        click.secho(f"❌ No docker-compose file found for {product} ({env}).", fg="red")
        raise SystemExit(1)

    project_name = _compose_project_name(product, env)

    if service:
        # Use docker compose exec directly by service name
        exec_cmd = ["docker", "compose", "-p", project_name, "-f", compose_file, "exec", service]
        if cmd:
            exec_cmd += ["sh", "-c", cmd]
        else:
            exec_cmd += ["bash"]
        try:
            subprocess.run(exec_cmd, check=False)
        except FileNotFoundError:
            exec_cmd[-1] = "sh"
            subprocess.run(exec_cmd, check=False)
        return

    container_id = _find_main_container(project_name, compose_file, docker_dir)

    if not container_id:
        click.secho(f"❌ No running containers found for {product} ({env}).", fg="red")
        click.secho("   Run: splent product:up --" + env, fg="yellow")
        raise SystemExit(1)

    click.secho(f"🐚 Opening shell in container {container_id[:12]} ({env})...", fg="cyan")

    shell_cmd = ["docker", "exec", "-it", container_id]
    if cmd:
        shell_cmd += ["sh", "-c", cmd]
    else:
        shell_cmd.append("bash")

    result = subprocess.run(shell_cmd, check=False)
    if result.returncode == 126 or result.returncode == 127:
        # bash not found, try sh
        shell_cmd[-1] = "sh"
        subprocess.run(shell_cmd, check=False)


cli_command = product_shell
