import os
import subprocess
import threading
import click
import tomllib
import os, tomllib, subprocess, click

from collections import OrderedDict

def clone_missing_features(product_path, workspace="/workspace"):
    
    def is_splent_developer():
        if os.getenv("SPLENT_USE_SSH", "").lower() == "true": return True
        if os.getenv("SPLENT_ROLE", "").lower() == "developer": return True
        try:
            r = subprocess.run(["ssh","-T","git@github.com"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
            return "successfully authenticated" in r.stderr.lower()
        except Exception:
            return False

    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py):
        click.echo(f"❌ pyproject.toml not found at {product_path}")
        return []

    with open(py, "rb") as f:
        data = tomllib.load(f)

    # ← tu formato real
    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not features:
        click.echo("ℹ️ No features declared under [project.optional-dependencies.features]")
        return []

    use_ssh = is_splent_developer()
    click.echo(f"🔑 Cloning mode: {'SSH' if use_ssh else 'HTTPS'}")

    cloned = []
    for feature in features:
        org = "splent-io"
        url = (f"git@github.com:{org}/{feature}.git" if use_ssh
               else f"https://github.com/{org}/{feature}.git")

        dst = os.path.join(workspace, feature)
        if os.path.exists(dst):
            click.echo(f"✅ {feature} already present at {dst}")
            continue

        click.echo(f"⬇️ Cloning {feature} from {url}")
        r = subprocess.run(["git","clone","--depth","1",url,dst],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if r.returncode != 0:
            click.echo(f"⚠️  Failed to clone {feature} from {url}")
            click.echo(r.stderr.strip())
            continue
        click.echo(f"✅ Cloned {feature}")
        cloned.append(feature)

    if cloned:
        uniq = sorted(set(cloned))
        click.echo(f"✨ Cloned {len(uniq)} new feature(s): {', '.join(uniq)}")

    # Devolvemos la lista “oficial” del pyproject (no solo las clonadas)
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
