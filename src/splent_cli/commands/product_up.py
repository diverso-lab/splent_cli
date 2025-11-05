import os
import subprocess
import threading
import click
import tomllib
from collections import OrderedDict

def is_splent_developer() -> bool:
    return os.getenv("SPLENT_USE_SSH", "").lower() == "true"

def clone_missing_features(product_path, workspace="/workspace"):
    """Clona automáticamente las features listadas en project.optional-dependencies.features."""
    pyproject_file = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(pyproject_file):
        click.echo(f"❌ pyproject.toml not found at {product_path}")
        return []

    with open(pyproject_file, "rb") as f:
        pyproject = tomllib.load(f)

    # Leer lista de features estándar
    features = pyproject.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not features:
        click.echo("ℹ️ No features declared under [project.optional-dependencies.features]")
        return []

    use_ssh = is_splent_developer()
    click.echo(f"🔑 Cloning mode: {'SSH' if use_ssh else 'HTTPS'}")

    cloned = []
    for feature in features:
        org = "splent-io"
        repo_url = (
            f"git@github.com:{org}/{feature}.git"
            if use_ssh
            else f"https://github.com/{org}/{feature}.git"
        )

        feature_path = os.path.join(workspace, feature)
        if os.path.exists(feature_path):
            click.echo(f"✅ {feature} already present at {feature_path}")
            continue

        click.echo(f"⬇️ Cloning {feature} from {repo_url}")
        
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, feature_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            click.echo(f"⚠️  Failed to clone {feature} from {repo_url}")
            click.echo(result.stderr.strip())
            continue  # saltar a la siguiente feature
        else:
            click.echo(f"✅ Cloned {feature}")
            cloned.append(feature)

        cloned.append(feature)

    if cloned:
        click.echo(f"✨ Cloned {len(cloned)} new feature(s): {', '.join(cloned)}")

    return features


# === UTILIDADES EXISTENTES ======================================

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


# === COMANDO PRINCIPAL ==========================================

@click.command("product:up")
@click.argument("product", required=False)
@click.option("--env", default=None, type=click.Choice(["dev", "prod"]), help="Docker environment: dev or prod")
def product_up(product, env):
    """
    Starts a SPLENT product and its activated features using the appropriate docker-compose files.
    """
    workspace = "/workspace"
    workspace_env = os.path.join(workspace, ".env")

    # Si no se pasa el argumento, usar SPLENT_APP del .env global
    if not product:
        if os.path.exists(workspace_env):
            with open(workspace_env, "r") as f:
                for line in f:
                    if line.startswith("SPLENT_APP="):
                        product = line.strip().split("=", 1)[1]
                        break
        if not product:
            click.echo("❌ No product specified and SPLENT_APP not set in /workspace/.env")
            return

    product_path = os.path.join(workspace, product)

    # Si no se especifica entorno (--env), preguntar
    if env is None:
        env = click.prompt("Select environment", type=click.Choice(["dev", "prod"]), default="dev")

    # === NUEVO: Clonar features automáticamente antes de continuar
    features_list = clone_missing_features(product_path, workspace)

    # 1. Generar o crear los .env de producto y features
    docker_dirs = [(product, os.path.join(product_path, "docker"))] + [
        (feature, os.path.join(workspace, feature, "docker")) for feature in features_list
    ]

    for name, docker_dir in docker_dirs:
        if not os.path.exists(docker_dir):
            continue

        env_file = os.path.join(docker_dir, ".env")
        if not os.path.exists(env_file):
            click.echo(f"⚠️  No .env found for {name}. Using environment '{env}'.")
            example_file = os.path.join(docker_dir, f".env.{env}.example")

            if os.path.exists(example_file):
                subprocess.run(["cp", example_file, env_file], check=True)
                click.echo(f"📄 Created {name}/docker/.env from {os.path.basename(example_file)}")
            else:
                click.echo(f"❌ No example file found for {env} environment in {docker_dir}")
                return
        else:
            click.echo(f"ℹ️  Using existing {name}/docker/.env")

    # 2. Fusionar variables de entorno
    product_env_path = os.path.join(product_path, "docker", ".env")
    feature_env_paths = [os.path.join(workspace, feature, "docker", ".env") for feature in features_list]
    merge_feature_envs_into_product_env(product_env_path, feature_env_paths)
    
    # 3. Confirmar si es producción
    ensure_env_reviewed_if_prod(env, product_env_path)

    # 4. Levantar el producto y sus features
    launch_compose(product, os.path.join(product_path, "docker"), env)

    for feature in features_list:
        launch_compose(feature, os.path.join(workspace, feature, "docker"), env)

    # 5. Ejecutar entrypoint
    execute_entrypoint(product, product_path, env)

    click.echo("✅ All services started successfully.")
