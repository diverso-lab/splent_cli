import os
import tomllib
import tomli_w
import click
from splent_cli.services import context, compose
from splent_cli.utils.manifest import feature_key, set_feature_state


@click.command(
    "feature:attach",
    short_help="Attach a cached feature version to the current product.",
)
@click.argument("feature_identifier", required=True)
@click.argument("version", required=True)
def feature_attach(feature_identifier, version):
    """
    Attach a cached feature version to the current product.

    - Requires the feature to already be in the local cache.
      If not, run: splent feature:clone <namespace>/<feature>@<version>
    - Updates pyproject.toml referencing feature@version.
    - Creates/updates the versioned symlink in features/<namespace>/.
    - Updates the manifest state to 'declared'.
    """
    product = context.require_app()
    ws = context.workspace()

    # --- Parse feature identifier -------------------------------------------
    namespace, namespace_github, namespace_fs, feature_name = (
        compose.parse_feature_identifier(feature_identifier)
    )

    cache_base = str(ws / ".splent_cache" / "features" / namespace_fs)
    product_path = str(ws / product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product.")
        raise SystemExit(1)

    # --- 1️⃣ Verify feature exists in cache ---------------------------------
    versioned_dir = os.path.join(cache_base, f"{feature_name}@{version}")

    if not os.path.exists(versioned_dir):
        click.echo(
            f"❌ Feature '{namespace}/{feature_name}@{version}' not found in cache.\n"
            f"   Run first: splent feature:clone {namespace}/{feature_name}@{version}"
        )
        raise SystemExit(1)

    click.echo(f"✅ Cache found → {versioned_dir}")

    # --- 2️⃣ Update pyproject.toml ------------------------------------------
    full_name = f"{namespace}/{feature_name}@{version}"
    bare_name = f"{namespace}/{feature_name}"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    from splent_cli.utils.feature_utils import (
        read_features_from_data,
        write_features_to_data,
    )

    features = read_features_from_data(data)

    if full_name in features:
        click.echo(f"ℹ️  Feature '{full_name}' already present in pyproject.toml.")
    else:
        # Replace bare entry (added by uvl:sync) or old versioned entry if present
        features = [
            f for f in features if f != bare_name and not f.startswith(f"{bare_name}@")
        ]
        features.append(full_name)
        write_features_to_data(data, features)
        with open(pyproject_path, "wb") as f:
            tomli_w.dump(data, f)
        click.echo(f"🧩 Updated pyproject.toml → {full_name}")

    # --- 3️⃣ Create/update symlink ------------------------------------------
    product_features_dir = os.path.join(product_path, "features", namespace_fs)
    os.makedirs(product_features_dir, exist_ok=True)

    new_link = os.path.join(product_features_dir, f"{feature_name}@{version}")
    if os.path.islink(new_link):
        os.unlink(new_link)
    rel_target = os.path.relpath(versioned_dir, product_features_dir)
    os.symlink(rel_target, new_link)

    click.echo(f"🔗 Linked {new_link} → {rel_target}")

    # --- 4️⃣ Update manifest ------------------------------------------------
    key = feature_key(namespace_fs, feature_name, version)
    set_feature_state(
        product_path,
        product,
        key,
        "declared",
        namespace=namespace_fs,
        name=feature_name,
        version=version,
        mode="pinned",
    )

    click.echo("🎯 Feature successfully attached.")
