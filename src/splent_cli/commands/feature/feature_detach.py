import os
import re
import click


def parse_feature_identifier(identifier: str):
    if "/" in identifier:
        namespace, fname = identifier.split("/", 1)
    else:
        namespace = "splent-io"
        fname = identifier

    namespace_github = namespace.replace("_", "-")
    namespace_fs = namespace.replace("-", "_")

    return namespace, namespace_github, namespace_fs, fname


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
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    # --- Parse namespace + feature -----------------------------------------
    namespace, namespace_github, namespace_fs, feature_name = \
        parse_feature_identifier(feature_identifier)

    product_path = os.path.join(workspace, product)
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
        click.echo(f"⚠️ No symlink found for {feature_name}@{version} in {namespace_fs}/")

    click.echo("🎯 Feature successfully detached.")
