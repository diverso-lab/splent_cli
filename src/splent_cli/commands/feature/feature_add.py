import os
import tomllib
import tomli_w
import click
from splent_cli.services import context
from splent_cli.utils.manifest import feature_key, set_feature_state


@click.command(
    "feature:add",
    short_help="Adds a local (non-versioned) feature to the active product.",
)
@click.argument("full_name", required=True)
def feature_add(full_name):
    """
    Adds a local feature (no version, no repo) to the current SPLENT product.
    The feature name must be in the format <namespace>/<feature_name>.

    Example:
      splent feature:add drorganvidez/notepad
    """

    # --------------------------
    # 0️⃣ Validate format
    # --------------------------
    if "/" not in full_name:
        click.echo("❌ Invalid format. Use: <namespace>/<feature_name>")
        raise SystemExit(1)

    namespace, feature_name = full_name.split("/", 1)
    org_safe = namespace.replace("-", "_")

    workspace = str(context.workspace())
    product = context.require_app()

    cache_dir = os.path.join(
        workspace, ".splent_cache", "features", org_safe, feature_name
    )
    if not os.path.exists(cache_dir):
        click.echo(f"❌ Feature not found in cache: {cache_dir}")
        raise SystemExit(1)

    # --------------------------
    # 1️⃣ Update pyproject.toml
    # --------------------------
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product directory.")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    project = data.setdefault("project", {})
    optional_deps = project.setdefault("optional-dependencies", {})
    features = optional_deps.setdefault("features", [])

    if full_name not in features:
        features.append(full_name)
        with open(pyproject_path, "wb") as f:
            tomli_w.dump(data, f)
        click.echo(f"🧩 Added '{full_name}' to pyproject.toml.")
    else:
        click.echo(f"ℹ️ Feature '{full_name}' already present in pyproject.toml.")

    # --------------------------
    # 2️⃣ Create symlink
    # --------------------------
    product_features_dir = os.path.join(workspace, product, "features", org_safe)
    os.makedirs(product_features_dir, exist_ok=True)

    link_path = os.path.join(product_features_dir, feature_name)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)

    os.symlink(cache_dir, link_path)
    click.echo(f"🔗 Linked {link_path} → {cache_dir}")

    # --------------------------
    # 3️⃣ Update manifest
    # --------------------------
    product_path = os.path.join(workspace, product)
    key = feature_key(namespace, feature_name)
    set_feature_state(
        product_path, product, key, "declared",
        namespace=namespace, name=feature_name, version=None, mode="editable",
    )

    click.echo(f"✅ Feature '{full_name}' added successfully to product '{product}'.")
