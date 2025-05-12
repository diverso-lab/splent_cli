import os
import subprocess
import threading
import click
import tomllib
from collections import OrderedDict

def load_features(product_path):
    pyproject_file = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(pyproject_file):
        click.echo(f"❌ pyproject.toml not found at {product_path}")
        return []

    with open(pyproject_file, "rb") as f:
        pyproject = tomllib.load(f)

    return pyproject.get("project", {}).get("optional-dependencies", {}).get("features", [])

def generate_env_file(name, docker_dir, env):
    env_example = os.path.join(docker_dir, f".env.{env}.example")
    fallback_example = os.path.join(docker_dir, ".env.example")
    env_file = os.path.join(docker_dir, ".env")

    selected = env_example if os.path.exists(env_example) else fallback_example if os.path.exists(fallback_example) else None

    if selected:
        with open(selected, "r") as f:
            content = f.read()
        with open(env_file, "w") as f:
            f.write(content)
        click.echo(f"📄 {name}: .env generated from {os.path.basename(selected)}")
    else:
        click.echo(f"⚠️  {name}: No .env.{env}.example or .env.example found, skipping .env generation")

def merge_feature_envs_into_product_env(product_env_path, feature_env_paths):
    def parse_env_file(path):
        env_dict = OrderedDict()
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_dict[key.strip()] = value.strip()
        return env_dict

    if not os.path.exists(product_env_path):
        click.echo(f"❌ Product .env not found at {product_env_path}")
        return

    product_env = parse_env_file(product_env_path)

    for feature_env in feature_env_paths:
        if os.path.exists(feature_env):
            feature_vars = parse_env_file(feature_env)
            for key, value in feature_vars.items():
                if key not in product_env:
                    product_env[key] = value

    with open(product_env_path, "w") as f:
        for key, value in product_env.items():
            f.write(f"{key}={value}\n")
    click.echo("🔗 Merged feature .env values into product .env")

def launch_compose(name, docker_dir, env):
    preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback = os.path.join(docker_dir, "docker-compose.yml")

    if os.path.exists(preferred):
        compose_file = preferred
    elif os.path.exists(fallback):
        compose_file = fallback
    else:
        click.echo(f"⚠️  {name}: No docker-compose.{env}.yml or docker-compose.yml found in {docker_dir}")
        return

    click.echo(f"⬆️  {name}: Launching {os.path.basename(compose_file)}")
    subprocess.run(["docker", "compose", "-f", compose_file, "up", "-d"], cwd=docker_dir, check=True)

def stream_logs(container_name):
    cmd = [
        "docker", "exec", container_name,
        "bash", "-c", "tail -f /var/log/entrypoint.log"
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def print_output():
        for line in proc.stdout:
            print(line, end="")
    threading.Thread(target=print_output, daemon=True).start()

def execute_entrypoint(product, product_path, env):
    entrypoint = os.path.join(product_path, "entrypoints", f"entrypoint.{env}.sh")
    if not os.path.exists(entrypoint):
        click.echo(f"⚠️  entrypoint.{env}.sh not found in {product}/entrypoints")
        return

    container_name = f"{product}_web"
    click.echo(f"🚀 Running entrypoint in {container_name}: {os.path.basename(entrypoint)}")

    subprocess.run([
        "docker", "exec", "-i", container_name,
        "bash", "-c", f"bash {entrypoint}"
    ])
    
    stream_logs(container_name)
    
def ensure_env_reviewed_if_prod(env, env_path):
    if env == "prod":
        click.echo("\n⚠️  You are about to launch in production mode.")
        click.echo(f"📄 Please review the merged .env file at:\n   {env_path}")
        click.confirm("Have you reviewed and customized this .env file?", abort=True)



@click.command("product:up")
@click.argument("product")
@click.option("--env", default="dev", type=click.Choice(["dev", "prod"]), help="Docker environment: dev or prod")
def product_up(product, env):
    """
    Starts a SPLENT product and its activated features using the appropriate docker-compose files.
    """
    workspace = "/workspace"
    product_path = os.path.join(workspace, product)

    features_list = load_features(product_path)

    docker_dirs = [(product, os.path.join(product_path, "docker"))] + [
        (feature, os.path.join(workspace, feature, "docker")) for feature in features_list
    ]

    # 1. Generate .env files
    for name, docker_dir in docker_dirs:
        if os.path.exists(docker_dir):
            generate_env_file(name, docker_dir, env)

    # 2. Merge .env values from features into the product's .env
    product_env_path = os.path.join(product_path, "docker", ".env")
    feature_env_paths = [os.path.join(workspace, feature, "docker", ".env") for feature in features_list]
    merge_feature_envs_into_product_env(product_env_path, feature_env_paths)
    
    # 2.5. Confirmar en producción antes de continuar
    ensure_env_reviewed_if_prod(env, product_env_path)

    # 3. Launch product and features
    launch_compose(product, os.path.join(product_path, "docker"), env)

    for feature in features_list:
        launch_compose(feature, os.path.join(workspace, feature, "docker"), env)

    # 4. Execute entrypoint
    execute_entrypoint(product, product_path, env)

    click.echo("✅ All services started successfully.")
