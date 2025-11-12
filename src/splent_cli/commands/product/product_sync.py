import os
import tomllib
import subprocess
import shutil
import click


@click.command(
    "product:sync",
    help="Sync (clone + link) all versioned features declared in the active product.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Force reclone even if feature already exists in cache.",
)
def product_sync(force):
    """
    Sync all versioned features declared in the current product's pyproject.toml:
    - Clones each remote feature into cache (if missing, or if --force)
    - Creates symlinks under /workspace/<product>/features/<namespace>/.
    """
    workspace = os.getenv("WORKING_DIR", "/workspace")
    product = os.getenv("SPLENT_APP")

    if not product:
        click.secho(
            "❌ SPLENT_APP not defined. Please select a product first.", fg="red"
        )
        raise SystemExit(1)

    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.secho(f"❌ pyproject.toml not found for product '{product}'", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = (
        data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    )
    if not features:
        click.secho(
            "ℹ️ No features declared under [project.optional-dependencies.features].",
            fg="yellow",
        )
        return

    remote_features = [f for f in features if "@" in f]
    local_features = [f for f in features if "@" not in f]

    if not remote_features:
        click.secho("ℹ️ No versioned (remote) features found to sync.", fg="yellow")
        return

    if local_features:
        click.secho(
            f"🧱 Skipping {len(local_features)} local features (no version).", fg="cyan"
        )

    click.secho(
        f"🔄 Syncing {len(remote_features)} features for '{product}'... (force={force})\n",
        fg="green",
    )

    for entry in remote_features:
        # 1️⃣ Validación y extracción flexible
        if "@" not in entry:
            click.secho(f"🧱 Skipping local feature (no version): {entry}", fg="cyan")
            continue

        if "/" in entry:
            namespace, rest = entry.split("/", 1)
        else:
            namespace = "splent-io"  # namespace por defecto
            rest = entry

        repo, _, version = rest.partition("@")
        version = version or "main"

        namespace_safe = namespace.replace("-", "_").replace(".", "_")

        # 2️⃣ Ruta en caché
        cache_dir = os.path.join(
            workspace, ".splent_cache", "features", namespace_safe, f"{repo}@{version}"
        )
        product_features_dir = os.path.join(
            workspace, product, "features", namespace_safe
        )
        link_path = os.path.join(product_features_dir, f"{repo}@{version}")

        # 3️⃣ Clonado con --force
        if os.path.exists(cache_dir):
            if force:
                click.secho(f"♻️ Removing existing cache: {cache_dir}", fg="yellow")
                shutil.rmtree(cache_dir)
            else:
                click.secho(f"✅ Using cached {namespace}/{repo}@{version}", fg="cyan")
                _create_symlink(cache_dir, product_features_dir, link_path)
                continue

        # 4️⃣ Clonado
        cmd = ["splent", "feature:clone", f"{namespace}/{repo}@{version}"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            click.secho(
                f"❌ Failed to clone {namespace}/{repo}@{version}:\n{result.stderr}",
                fg="red",
            )
            continue

        # 5️⃣ Crear symlink
        _create_symlink(cache_dir, product_features_dir, link_path)

        cache_dir = os.path.join(
            workspace, ".splent_cache", "features", namespace_safe, f"{repo}@{version}"
        )
        product_features_dir = os.path.join(
            workspace, product, "features", namespace_safe
        )
        link_path = os.path.join(product_features_dir, f"{repo}@{version}")

        # 1️⃣ Clone feature (respecting --force)
        if os.path.exists(cache_dir):
            if force:
                click.secho(f"♻️ Removing existing cache: {cache_dir}", fg="yellow")
                shutil.rmtree(cache_dir)
            else:
                click.secho(f"✅ Using cached {namespace}/{repo}@{version}", fg="cyan")
                # ensure link still created even if cached
                _create_symlink(cache_dir, product_features_dir, link_path)
                continue

        cmd = ["splent", "feature:clone", f"{namespace}/{repo}@{version}"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            click.secho(f"❌ Failed to clone {entry}:\n{result.stderr}", fg="red")
            continue

        # 2️⃣ Create symlink after clone
        _create_symlink(cache_dir, product_features_dir, link_path)

    click.secho(
        "\n✅ Product synced successfully — all features cloned and linked.", fg="green"
    )


def _create_symlink(cache_dir, product_features_dir, link_path):
    """Ensure feature symlink is created and up to date."""
    os.makedirs(product_features_dir, exist_ok=True)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)
    os.symlink(cache_dir, link_path)
    click.secho(f"🔗 Linked {link_path} → {cache_dir}", fg="cyan")
