import os
import shutil
import subprocess
import click
import tomllib
from pathlib import Path


def _compose_project_name(name: str, env: str) -> str:
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def _normalize_feature_ref(feat: str) -> str:
    if "features/" in feat:
        feat = feat.split("features/")[-1]
    if "/" not in feat:
        feat = f"splent_io/{feat}"
    return feat


def _feature_cache_docker_dir(workspace: str, feature: str) -> str:
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


def _docker_down(name: str, docker_dir: str, env: str):
    compose_preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    compose_fallback = os.path.join(docker_dir, "docker-compose.yml")
    compose_file = compose_preferred if os.path.exists(compose_preferred) else compose_fallback
    if not os.path.exists(compose_file):
        return
    project_name = _compose_project_name(name, env)
    subprocess.run(
        ["docker", "compose", "-p", project_name, "-f", compose_file, "down", "-v"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    click.echo(f"  🛑  {name} stopped and volumes removed.")


@click.command("product:clean", short_help="Full reset: stop containers, wipe volumes, reset DB, clear files.")
@click.option("--dev", "env_dev", is_flag=True, help="Use development environment.")
@click.option("--prod", "env_prod", is_flag=True, help="Use production environment.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def product_clean(env_dev, env_prod, yes):
    """
    Nuclear reset of the active product's environment:

    \b
    1. Docker Compose down --volumes (product + all features)
    2. Database reset with migration regeneration
    3. Clear uploads directory
    4. Clear application log

    Use when you want a completely clean slate.
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

    click.secho(f"\n⚠️  This will completely wipe {product} ({env}):", fg="yellow")
    click.echo("  - Stop all Docker containers and remove volumes")
    click.echo("  - Reset the database and regenerate migrations")
    click.echo("  - Clear uploads and application log")
    click.echo()

    if not yes and not click.confirm("Continue?"):
        click.echo("❎ Cancelled.")
        raise SystemExit(0)

    product_path = os.path.join(workspace, product)

    # ── 1. Docker down --volumes ─────────────────────────────────────
    click.secho("\n🐳 Stopping containers and removing volumes...", fg="cyan")

    pyproject_path = os.path.join(product_path, "pyproject.toml")
    features = []
    if os.path.exists(pyproject_path):
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])

    _docker_down(product, os.path.join(product_path, "docker"), env)
    for feat in features:
        clean = _normalize_feature_ref(feat)
        _docker_down(clean, _feature_cache_docker_dir(workspace, clean), env)

    # ── 2. DB reset ──────────────────────────────────────────────────
    click.secho("\n🗄️  Resetting database...", fg="cyan")
    result = subprocess.run(
        ["splent", "db:reset", "--clear-migrations", "--yes"],
        check=False,
    )
    if result.returncode != 0:
        click.secho("  ⚠️  db:reset exited with errors — check output above.", fg="yellow")
    else:
        click.secho("  ✔ Database reset.", fg="green")

    # ── 3. Clear uploads ─────────────────────────────────────────────
    click.secho("\n🗂️  Clearing uploads...", fg="cyan")
    result = subprocess.run(["splent", "clear:uploads"], check=False)
    if result.returncode == 0:
        click.secho("  ✔ Uploads cleared.", fg="green")

    # ── 4. Clear log ─────────────────────────────────────────────────
    click.secho("\n📋 Clearing application log...", fg="cyan")
    result = subprocess.run(["splent", "clear:log"], check=False)
    if result.returncode == 0:
        click.secho("  ✔ Log cleared.", fg="green")

    click.echo()
    click.secho(f"✅ {product} ({env}) fully cleaned. Run 'splent product:up' to start fresh.", fg="green")


cli_command = product_clean
