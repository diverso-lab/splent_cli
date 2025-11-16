import click
import subprocess
import yaml
import re
from pathlib import Path

WORKSPACE = Path("/workspace")


# ---------------------------------------------------------
# ENV LOADERS
# ---------------------------------------------------------

def load_global_env():
    """
    Load /workspace/.env (global). Only needed for SPLENT_APP and global settings.
    """
    env_file = WORKSPACE / ".env"
    if not env_file.exists():
        raise click.ClickException("Missing /workspace/.env")

    env = {}
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v
    return env


def load_product_env(app_name: str):
    """
    Load product-specific .env:
    /workspace/<app>/docker/.env
    """
    product_env_file = WORKSPACE / app_name / "docker" / ".env"

    if not product_env_file.exists():
        raise click.ClickException(f"Missing {product_env_file}")

    env = {}
    for line in product_env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k] = v
    return env


# ---------------------------------------------------------
# ENV RESOLUTION
# ---------------------------------------------------------

def resolve_env(user_env: str | None, product_env: dict):
    """
    Determine which environment should be used:

    1. --env flag
    2. SPLENT_ENV in product .env
    3. default = dev
    """
    if user_env:
        return user_env.lower()

    if "SPLENT_ENV" in product_env:
        return product_env["SPLENT_ENV"].lower()

    return "dev"


# ---------------------------------------------------------
# COMPOSE LOADERS
# ---------------------------------------------------------

def resolve_compose_path(app: str, env: str):
    """
    Compose files in a product:

    /workspace/<app>/docker/docker-compose.<env>.yml
    /workspace/<app>/docker/docker-compose.yml  (fallback)
    """

    base = WORKSPACE / app / "docker"

    candidate = base / f"docker-compose.{env}.yml"
    fallback = base / "docker-compose.yml"

    if candidate.exists():
        return candidate

    if fallback.exists():
        return fallback

    raise click.ClickException(
        f"No compose file found for environment '{env}'. "
        f"Checked: {candidate} and {fallback}"
    )


def load_compose(compose_path: Path):
    try:
        with open(compose_path) as f:
            return yaml.safe_load(f)
    except Exception as e:
        raise click.ClickException(f"Error loading compose file: {e}")


def find_app_service(app: str, compose: dict):
    """
    Find the service whose name contains the product name.
    Example: product 'prueba' → service 'prueba_web'.
    """
    services = compose.get("services", {})

    for name in services:
        if app.lower() in name.lower():
            return name, services[name]

    raise click.ClickException(
        f"No service for product '{app}' found in compose file."
    )

# ---------------------------------------------------------
# HOST IP
# ---------------------------------------------------------

def detect_host_ip():
    """
    Detect whether we are running inside a Vagrant environment.
    In Vagrant → /workspace/.vagrant exists.
    """
    if Path("/workspace/.vagrant").exists():
        return "10.10.10.10"
    return "localhost"

# ---------------------------------------------------------
# DOCKER RUNTIME
# ---------------------------------------------------------

def get_runtime_ports(container_name: str):
    """
    Parse 'docker ps' to extract the published ports of a container.
    """
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}} {{.Ports}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise click.ClickException(result.stderr)

    for line in result.stdout.splitlines():
        if line.startswith(container_name):
            mapping = line[len(container_name):].strip()

            # Example mapping: "0.0.0.0:5435->5000/tcp"
            match = re.search(r"(\d+)->(\d+)", mapping)
            if match:
                external, internal = match.groups()
                return external, internal

            raise click.ClickException(
                f"Container '{container_name}' found but port format is unrecognized: {mapping}"
            )

    raise click.ClickException(
        f"Container '{container_name}' not running or has no published ports."
    )


# ---------------------------------------------------------
# COMMAND
# ---------------------------------------------------------

@click.command("product:port", short_help="Show the product URL and PORT.")
@click.option("--env", "env_flag", help="Environment to inspect (dev/prod).")
def product_port(env_flag):
    """
    Show the real port where the product web service is running.
    """
    # 1. Load /workspace/.env
    global_env = load_global_env()

    app = global_env.get("SPLENT_APP")
    if not app:
        raise click.ClickException("SPLENT_APP missing from /workspace/.env")

    # 2. Load /workspace/<app>/docker/.env
    product_env = load_product_env(app)

    # 3. Resolve environment (dev/prod)
    resolved_env = resolve_env(env_flag, product_env)

    # 4. Find compose file (inside /docker)
    compose_path = resolve_compose_path(app, resolved_env)
    compose = load_compose(compose_path)

    # 5. Locate the service of the product
    service_name, _ = find_app_service(app, compose)

    # 6. Extract runtime ports from Docker
    external_port, internal_port = get_runtime_ports(service_name)

    # 7. Print result
    host_ip = detect_host_ip()

    click.echo(f"Product: {app}")
    click.echo(f"Environment: {resolved_env}")
    click.echo(f"Compose file: {compose_path.name}")
    click.echo(f"Service: {service_name}")
    click.echo(f"Container: {service_name}")
    click.echo(f"Internal port: {internal_port}")
    click.echo(f"External port: {external_port}")
    click.echo()
    click.echo(f"URL: http://{host_ip}:{external_port}")