import os
import subprocess
import tomllib
import click


@click.command("feature:env")
@click.argument("feature_name", required=True)
@click.option("--generate", is_flag=True, help="Generate the .env file for the given feature.")
@click.option("--dev", "env_name", flag_value="dev", help="Use development template (.env.dev.example).")
@click.option("--prod", "env_name", flag_value="prod", help="Use production template (.env.prod.example).")
def feature_env(feature_name, generate, env_name):
    """
    Manage environment files for a feature (via symlink inside the active product).

    Example:
        splent feature:env splent_feature_auth --generate --dev
    """
    workspace = "/workspace"
    org_safe = "splent_io"
    product = os.getenv("SPLENT_APP")

    if not product:
        click.echo("❌ SPLENT_APP not defined. Please select a product first.")
        raise SystemExit(1)

    # 1️⃣ Leer el pyproject.toml del producto
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.echo(f"❌ pyproject.toml not found at {pyproject_path}")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    feature_entries = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not feature_entries:
        click.echo("❌ No features declared in pyproject.toml.")
        raise SystemExit(1)

    # 2️⃣ Buscar la feature pedida con su versión
    feature_entry = next((f for f in feature_entries if f.startswith(feature_name)), None)
    if not feature_entry:
        click.echo(f"❌ Feature '{feature_name}' not found in [features] section of {pyproject_path}")
        raise SystemExit(1)

    # 3️⃣ Determinar entorno
    env_from_var = os.getenv("SPLENT_ENV")
    if not env_name and env_from_var:
        env_name = env_from_var
        click.echo(f"🌍 Using SPLENT_ENV={env_name}")
    elif not env_name:
        click.echo("❌ You must specify --dev, --prod, or define SPLENT_ENV=dev|prod.")
        raise SystemExit(1)

    # 4️⃣ Ruta del symlink del producto (con versión incluida)
    symlink_path = os.path.join(workspace, product, "features", org_safe, feature_entry)
    if not os.path.islink(symlink_path):
        click.echo(f"❌ Feature symlink not found for {feature_entry} in {symlink_path}")
        raise SystemExit(1)

    # Resolver el destino real
    feature_real_path = os.path.realpath(symlink_path)
    docker_dir = os.path.join(feature_real_path, "docker")

    if not os.path.exists(docker_dir):
        click.echo(f"📭 docker directory not found in feature path: {docker_dir}")
        raise SystemExit(1)

    env_file = os.path.join(docker_dir, ".env")

    if not generate:
        click.echo(f"📍 Feature: {feature_entry}")
        click.echo(f"🔗 Symlink: {symlink_path} → {feature_real_path}")
        click.echo(f"🧾 Env file: {env_file if os.path.exists(env_file) else '(not created)'}")
        raise SystemExit(0)

    # 5️⃣ Seleccionar plantilla
    example_specific = os.path.join(docker_dir, f".env.{env_name}.example")
    example_generic = os.path.join(docker_dir, ".env.example")

    if os.path.exists(env_file):
        click.echo(f"ℹ️  Existing .env found for {feature_entry}, skipping creation.")
        return

    selected = None
    if os.path.exists(example_specific):
        selected = example_specific
    elif os.path.exists(example_generic):
        selected = example_generic

    if selected:
        subprocess.run(["cp", selected, env_file], check=True)
        click.echo(f"📄 Created {feature_entry}/docker/.env from {os.path.basename(selected)}")
    else:
        click.echo(f"⚠️  No .env template found for {feature_entry} in {docker_dir}")
