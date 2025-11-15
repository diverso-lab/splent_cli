import os
import shutil
import click
from pathlib import Path


@click.command(
    "clear:features",
    short_help="Clears feature cache (.splent_cache/features) and broken symlinks in products.",
)
@click.option(
    "--namespace",
    default=None,
    help="Optional namespace (e.g. splent_io) to clear only that org.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def clear_features(namespace, yes):
    workspace = os.getenv("WORKING_DIR", "/workspace")
    cache_root = os.path.join(workspace, ".splent_cache", "features")

    if not os.path.exists(cache_root):
        click.echo("⚠️  No .splent_cache/features directory found.")
        raise SystemExit(0)

    target = cache_root if not namespace else os.path.join(cache_root, namespace)
    if not os.path.exists(target):
        click.echo(f"⚠️  Namespace '{namespace}' not found in cache.")
        raise SystemExit(0)

    if not yes and not click.confirm(
        f"⚠️  This will permanently delete {target} and cleanup symlinks. Continue?"
    ):
        click.echo("❎ Cancelled.")
        raise SystemExit(0)

    # 1️⃣ Borrar caché
    shutil.rmtree(target)
    os.makedirs(cache_root, exist_ok=True)
    click.secho(
        f"🧹 Feature cache cleared: {'all namespaces' if not namespace else namespace}",
        fg="green",
    )

    # 2️⃣ Limpiar symlinks rotos en productos
    products_dir = Path(workspace)
    removed_links = 0
    for product_dir in products_dir.iterdir():
        features_dir = product_dir / "features"
        if not features_dir.is_dir():
            continue

        for org_dir in features_dir.iterdir():
            if not org_dir.is_dir():
                continue

            for link in org_dir.iterdir():
                if link.is_symlink() and not link.exists():
                    link.unlink()
                    removed_links += 1

    if removed_links:
        click.secho(f"🔗 Removed {removed_links} broken feature symlinks.", fg="yellow")
    else:
        click.secho("✅ No broken symlinks found.", fg="green")
