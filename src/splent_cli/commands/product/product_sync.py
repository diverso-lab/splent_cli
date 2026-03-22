import os
import tomllib
import subprocess
import shutil
import click
from splent_cli.services import context


@click.command(
    "product:sync",
    short_help="Sync all versioned features declared in the active product."
)
@click.option(
    "--force",
    is_flag=True,
    help="Force reclone each feature (delete its cache folder first).",
)
def product_sync(force):
    workspace = str(context.workspace())
    product = context.require_app()

    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.secho(f"❌ pyproject.toml not found in product '{product}'", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])

    if not features:
        click.secho("ℹ️ No features declared.", fg="yellow")
        return

    # Only remote features have a version
    remote_features = [f for f in features if "@" in f]
    local_features = [f for f in features if "@" not in f]

    if local_features:
        click.secho(
            f"🧱 Skipping {len(local_features)} local features (no version).",
            fg="cyan",
        )

    click.secho(f"🔄 Syncing {len(remote_features)} remote features...\n", fg="green")

    for entry in remote_features:
        # Parse namespace/repo@version
        if "/" in entry:
            namespace, rest = entry.split("/", 1)
        else:
            namespace = "splent-io"
            rest = entry

        repo, _, version = rest.partition("@")
        version = version or "main"

        namespace_safe = namespace.replace("-", "_").replace(".", "_")

        cache_dir = os.path.join(
            workspace, ".splent_cache", "features", namespace_safe, f"{repo}@{version}"
        )
        product_features_dir = os.path.join(
            workspace, product, "features", namespace_safe
        )
        link_path = os.path.join(product_features_dir, f"{repo}@{version}")

        # 1️⃣ FORCE → clear **only** this feature cache
        if os.path.exists(cache_dir) and force:
            click.secho(f"♻️ Removing cached feature: {cache_dir}", fg="yellow")
            shutil.rmtree(cache_dir)

        # 2️⃣ Clone if missing
        if not os.path.exists(cache_dir):
            cmd = ["splent", "feature:clone", f"{namespace}/{repo}@{version}"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=os.environ.copy()
            )

            if result.returncode != 0:
                click.secho(f"❌ Failed to clone {entry}:\n{result.stderr}", fg="red")
                continue

        else:
            click.secho(f"✅ Using cached {namespace}/{repo}@{version}", fg="cyan")

        # 3️⃣ Create symlink
        _create_symlink(cache_dir, product_features_dir, link_path)

    click.secho("\n✅ Product synced successfully.", fg="green")


def _create_symlink(cache_dir, product_features_dir, link_path):
    os.makedirs(product_features_dir, exist_ok=True)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)
    os.symlink(cache_dir, link_path)
    click.secho(f"🔗 Linked {link_path} → {cache_dir}", fg="cyan")
