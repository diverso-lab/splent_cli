import os
import subprocess
import threading
import click
import tomllib
from collections import OrderedDict


def clone_missing_features(product_path, workspace="/workspace", default_version="v1.0.0"):
    """
    Clona las features declaradas en el pyproject.toml del producto,
    respetando la organización (org/feature@version) y normalizando guiones.
    La estructura resultante será:
      .splent_cache/features/<org_safe>/<feature>@<version>/
      <product>/features/<org_safe>/<feature>@<version> → symlink
    """

    # ----------------------------------------------
    # Helper: detectar modo SSH o HTTPS
    # ----------------------------------------------
    def is_splent_developer():
        if os.getenv("SPLENT_USE_SSH", "").lower() == "true":
            return True
        if os.getenv("SPLENT_ROLE", "").lower() == "developer":
            return True
        try:
            r = subprocess.run(
                ["ssh", "-T", "git@github.com"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3
            )
            return "successfully authenticated" in r.stderr.lower()
        except Exception:
            return False

    # ----------------------------------------------
    # Cargar pyproject.toml del producto
    # ----------------------------------------------
    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py):
        click.echo(f"❌ pyproject.toml not found at {product_path}")
        return []

    with open(py, "rb") as f:
        data = tomllib.load(f)

    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not features:
        click.echo("ℹ️ No features declared under [project.optional-dependencies.features]")
        return []

    # ----------------------------------------------
    # Preparar entorno
    # ----------------------------------------------
    use_ssh = is_splent_developer()
    click.echo(f"🔑 Cloning mode: {'SSH' if use_ssh else 'HTTPS'}")

    cache_base = os.path.join(workspace, ".splent_cache", "features")
    os.makedirs(cache_base, exist_ok=True)
    linked = []

    # ----------------------------------------------
    # Clonado y enlace
    # ----------------------------------------------
    for feature_entry in features:
        # 1️⃣ Parsear organización, nombre y versión
        if "/" in feature_entry:
            org, rest = feature_entry.split("/", 1)
        else:
            org, rest = "splent-io", feature_entry

        name, _, version = rest.partition("@")
        version = version or default_version

        org_safe = org.replace("-", "_")  # 🔥 clave para evitar errores en imports

        click.echo(f"🔍 Feature: {name}@{version} (org: {org_safe})")

        # 2️⃣ Construir URL de origen (usando el nombre original con guión)
        url = (
            f"git@github.com:{org}/{name}.git"
            if use_ssh
            else f"https://github.com/{org}/{name}.git"
        )

        # 3️⃣ Construir ruta de caché y crear si no existe
        cache_dir = os.path.join(cache_base, org_safe, f"{name}@{version}")
        os.makedirs(os.path.dirname(cache_dir), exist_ok=True)

        # 4️⃣ Clonar si no existe
        if not os.path.exists(cache_dir):
            click.echo(f"⬇️ Cloning {org}/{name}@{version} from {url}")
            r = subprocess.run(
                ["git", "clone", "--branch", version, "--depth", "1", url, cache_dir],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if r.returncode != 0:
                click.echo(f"⚠️ Failed to clone {org}/{name}@{version}")
                click.echo(r.stderr.strip())
                continue
            click.echo(f"✅ Cached {org_safe}/{name}@{version}")
        else:
            click.echo(f"✅ Using cached {org_safe}/{name}@{version}")

        # 5️⃣ Crear symlink en el producto
        product_features_dir = os.path.join(product_path, "features", org_safe)
        os.makedirs(product_features_dir, exist_ok=True)

        link_name = f"{name}@{version}"
        link_path = os.path.join(product_features_dir, link_name)

        if os.path.islink(link_path) or os.path.exists(link_path):
            click.echo(f"ℹ️ {link_name} already linked in {product_features_dir}")
        else:
            os.symlink(cache_dir, link_path)
            click.echo(f"🔗 Linked {link_name} → {cache_dir}")

        linked.append(f"{org_safe}/{link_name}")

    # ----------------------------------------------
    # Resultado final
    # ----------------------------------------------
    if linked:
        uniq = sorted(set(linked))
        click.echo(f"✨ Linked {len(uniq)} feature(s): {', '.join(uniq)}")

    return linked


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



def launch_compose(name: str, docker_dir: str, env: str):
    """
    Launch a Docker Compose environment for a given feature or product.
    Automatically redirects to the cached path under /workspace/.splent_cache/features if needed.
    """

    # 🔍 Resolve the correct cached path if the original comes from /workspace/<feature>/docker
    if ".splent_cache" not in docker_dir:
        parts = docker_dir.strip("/").split("/")
        try:
            if parts[-1] == "docker":
                org = parts[-3]
                feature_name = parts[-2]
            else:
                org = parts[-2]
                feature_name = parts[-1]

            docker_dir = os.path.join(
                "/workspace/.splent_cache/features",
                org,
                feature_name,
                "docker"
            )
        except Exception:
            click.echo(f"⚠️  Could not resolve cache path for {docker_dir}")
            return

    # 🧩 Look for docker-compose.<env>.yml or fallback to docker-compose.yml
    preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback = os.path.join(docker_dir, "docker-compose.yml")

    if os.path.exists(preferred):
        compose_file = preferred
    elif os.path.exists(fallback):
        compose_file = fallback
    else:
        # 🔇 Quietly skip features with no docker files
        click.echo(f"⚠️  {name}: No docker-compose.{env}.yml or docker-compose.yml found in {docker_dir}")
        return

    # 🏗️ Build a Docker-compatible project name (only [a-z0-9_-])
    splent_app = os.getenv("SPLENT_APP", name)
    flask_env = os.getenv("FLASK_ENV", env)
    raw_name = f"{splent_app}_{name}_{flask_env}"
    project_name = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in raw_name
    ).lower()

    click.echo(f"⬆️  {name}: Launching {os.path.basename(compose_file)}")
    click.echo(f"📂 Using docker dir: {docker_dir}")
    click.echo(f"🐳 Docker project: {project_name}")

    # 🚀 Compose command
    cmd = [
        "docker",
        "compose",
        "-p", project_name,
        "-f", compose_file,
        "up",
        "-d",
    ]

    try:
        subprocess.run(cmd, cwd=docker_dir, check=True)
        click.echo(f"✅  {name}: Containers started successfully.")
    except subprocess.CalledProcessError as e:
        click.echo(f"❌  Failed to launch {name}: {e}")
        return

    # 📦 Show active containers for this project
    click.echo("\n📦 Active containers:")
    subprocess.run(["docker", "ps", "--filter", f"name={project_name}"], check=False)



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
@click.option("--env", default="dev", type=click.Choice(["dev", "prod"]), help="Docker environment: dev or prod")
def product_up(product, env):
    workspace = "/workspace"
    workspace_env = os.path.join(workspace, ".env")

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

    # 0) Clonar features faltantes (devuelve la lista completa del pyproject)
    features_list = clone_missing_features(product_path, workspace)

    # 1) Generar .env (si no existe) para producto + features usando el MISMO env
    docker_dirs = [(product, os.path.join(product_path, "docker"))] + [
        (feature, os.path.join(workspace, feature, "docker")) for feature in features_list
    ]

    for name, docker_dir in docker_dirs:
        if not os.path.exists(docker_dir):
            continue
        env_file = os.path.join(docker_dir, ".env")
        if not os.path.exists(env_file):
            click.echo(f"⚠️  No .env found for {name}. Using environment '{env}'.")
            example = os.path.join(docker_dir, f".env.{env}.example")
            fallback = os.path.join(docker_dir, ".env.example")
            selected = example if os.path.exists(example) else (fallback if os.path.exists(fallback) else None)
            if selected:
                subprocess.run(["cp", selected, env_file], check=True)
                click.echo(f"📄 Created {name}/docker/.env from {os.path.basename(selected)}")
            else:
                click.echo(f"⚠️  No .env template found for {name} in {docker_dir} (skipping).")
        else:
            click.echo(f"ℹ️  Using existing {name}/docker/.env")

    # 2) Merge variables (si falta el .env del producto, no abortar)
    product_env = os.path.join(product_path, "docker", ".env")
    feature_envs = [os.path.join(workspace, feat, "docker", ".env") for feat in features_list]
    if os.path.exists(product_env):
        merge_feature_envs_into_product_env(product_env, feature_envs)
    else:
        click.echo(f"⚠️  Product .env not found at {product_env} (merge skipped).")
    
    # 3) Confirmación solo en prod
    ensure_env_reviewed_if_prod(env, product_env)

    # 4) Levantar compose: producto + features (si tienen docker-compose)
    launch_compose(product, os.path.join(product_path, "docker"), env)
    for feat in features_list:
        launch_compose(feat, os.path.join(workspace, feat, "docker"), env)

    # 5) Entrypoint del producto
    execute_entrypoint(product, product_path, env)

    click.echo("✅ All services started successfully.")
