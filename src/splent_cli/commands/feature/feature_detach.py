import os
import re
import click
from splent_cli.services import context, compose


@click.command(
    "feature:detach",
    short_help="Detach a versioned feature from the current product",
)
@click.argument("feature_identifier", required=True)
@click.argument("version", required=True)
def feature_detach(feature_identifier, version):
    """
    Detach a versioned feature from the active product.

    - Removes namespace/feature@version from pyproject.toml
    - Removes symlink in features/<namespace>/
    - Leaves .splent_cache intact
    """
    product = context.require_app()
    ws = context.workspace()

    # --- Parse namespace + feature -----------------------------------------
    namespace, namespace_github, namespace_fs, feature_name = (
        compose.parse_feature_identifier(feature_identifier)
    )

    product_path = str(ws / product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product.")
        raise SystemExit(1)

    # --- 1️⃣ Remove versioned reference from pyproject ----------------------
    click.echo(f"🧹 Removing {feature_name}@{version} from pyproject.toml...")

    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.escape(f"{feature_name}@{version}")
    new_content = re.sub(pattern, feature_name, content)

    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    click.echo("🧩 pyproject.toml cleaned.")

    # --- 2️⃣ Remove symlink --------------------------------------------------
    product_features_dir = os.path.join(product_path, "features", namespace_fs)
    link_path = os.path.join(product_features_dir, f"{feature_name}@{version}")

    if os.path.islink(link_path):
        os.unlink(link_path)
        click.echo(f"🔗 Removed symlink: {link_path}")
    else:
        click.echo(
            f"⚠️ No symlink found for {feature_name}@{version} in {namespace_fs}/"
        )

    click.echo("🎯 Feature successfully detached.")
