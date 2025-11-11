import os
import tomllib
import tomli_w
import click
from splent_cli.utils.path_utils import PathUtils


@click.command("feature:add", help="Adds a local (non-versioned) feature to the active product.")
@click.argument("feature_name", required=True)
@click.option("--namespace", "-n", help="Namespace (defaults to GITHUB_USER or 'splent-io').")
def feature_add(feature_name, namespace):
    """
    Adds a local feature (no version, no repo) to the current SPLENT product:
    - Verifies product and cache
    - Adds entry to [features] in pyproject.toml if missing
    - Creates symlink under /workspace/<product>/features/<namespace>/<feature_name>
    """

    workspace = PathUtils.get_working_dir()
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    github_user = os.getenv("GITHUB_USER")
    org = namespace or github_user or "splent-io"
    org_safe = org.replace("-", "_")

    cache_dir = os.path.join(workspace, ".splent_cache", "features", org_safe, feature_name)
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

    deps = data.get("project", {}).get("optional-dependencies", {})
    features = deps.get("features", [])

    if feature_name in features:
        click.echo(f"ℹ️ Feature '{feature_name}' already present in pyproject.toml.")
    else:
        features.append(feature_name)
        data["project"]["optional-dependencies"]["features"] = features
        with open(pyproject_path, "wb") as f:
            tomli_w.dump(data, f)
        click.echo(f"🧩 Added '{feature_name}' to pyproject.toml.")

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

    click.echo(f"✅ Feature '{feature_name}' added successfully to product '{product}' under namespace '{org}'.")
