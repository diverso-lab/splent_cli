import os
import tomllib
import subprocess
import click


@click.command("product:env")
@click.option("--generate", is_flag=True, help="Generate the .env file for the active product.")
@click.option("--dev", "env_name", flag_value="dev", help="Use .env.dev.example templates.")
@click.option("--prod", "env_name", flag_value="prod", help="Use .env.prod.example templates.")
@click.option("--all", "process_all", is_flag=True, help="Also process all features declared in pyproject.toml.")
def product_env(generate, env_name, process_all):
    """
    Generate or list .env files for the active product (and optionally all its features).

    Examples:
        splent product:env --generate --dev
        splent product:env --generate --prod
        splent product:env --generate --all --dev
    """
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")

    if not product:
        click.echo("❌ SPLENT_APP not defined. Please select a product first.")
        raise SystemExit(1)

    py_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(py_path):
        click.echo(f"❌ pyproject.toml not found at {py_path}")
        raise SystemExit(1)

    # 1️⃣ Determinar entorno
    env_from_var = os.getenv("SPLENT_ENV")
    if not env_name and env_from_var:
        env_name = env_from_var
        click.echo(f"🌍 Using SPLENT_ENV={env_name}")
    elif not env_name:
        click.echo("❌ You must specify --dev, --prod, or define SPLENT_ENV=dev|prod.")
        raise SystemExit(1)

    # 2️⃣ Generar solo el .env del producto
    docker_dir = os.path.join(workspace, product, "docker")
    env_file = os.path.join(docker_dir, ".env")
    example_specific = os.path.join(docker_dir, f".env.{env_name}.example")
    example_generic = os.path.join(docker_dir, ".env.example")

    if not generate:
        click.echo(f"📍 Product: {product}")
        click.echo(f"🧾 Env file: {env_file if os.path.exists(env_file) else '(not created)'}")
        raise SystemExit(0)

    if not os.path.exists(docker_dir):
        click.echo(f"❌ docker directory not found at {docker_dir}")
        raise SystemExit(1)

    click.echo(f"🚀 Generating .env for product '{product}' ({env_name})...\n")

    if os.path.exists(env_file):
        click.echo(f"ℹ️  Existing .env found for {product}, skipping creation.")
    else:
        selected = None
        if os.path.exists(example_specific):
            selected = example_specific
        elif os.path.exists(example_generic):
            selected = example_generic

        if selected:
            subprocess.run(["cp", selected, env_file], check=True)
            click.echo(f"📄 Created {product}/docker/.env from {os.path.basename(selected)}")
        else:
            click.echo(f"⚠️  No .env template found for {product} in {docker_dir}")

    # 3️⃣ Solo si se pasa --all, procesar las features
    if not process_all:
        click.echo("\n✅ Product .env generation complete.")
        return

    # 4️⃣ Procesar todas las features si se pidió --all
    with open(py_path, "rb") as f:
        data = tomllib.load(f)

    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not features:
        click.echo("ℹ️ No features declared in pyproject.toml.")
        return

    click.echo(f"\n🚀 Generating .env files for {len(features)} features ({env_name})...\n")

    created, skipped, no_template, failed = 0, 0, 0, 0

    for f_entry in features:
        base_name = f_entry.split("@")[0]
        cmd = [
            "splent",
            "feature:env",
            base_name,
            "--generate",
            f"--{env_name}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = result.stdout.strip()

        # Limpiar y filtrar líneas vacías
        lines = [l.strip() for l in output.splitlines() if l.strip()]
        cleaned = "\n".join(lines)
        if cleaned:
            click.echo(cleaned)

        # Detección más precisa
        if "docker directory not found" in cleaned:
            no_template += 1
            continue

        if "No .env template" in cleaned or "⚠️" in cleaned:
            no_template += 1
            continue

        if result.returncode != 0:
            failed += 1
            continue

        if "Created" in cleaned:
            created += 1
        elif "Existing" in cleaned:
            skipped += 1
        else:
            skipped += 1

    click.echo("\n✅ Feature .env generation complete.")
    click.echo(f"   📄 Created: {created}")
    click.echo(f"   ⏩ Skipped: {skipped}")
    click.echo(f"   📭 No template: {no_template}")
    click.echo(f"   💥 Failed: {failed}")
