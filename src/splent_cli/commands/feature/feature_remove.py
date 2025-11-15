import os
import tomllib
import tomli_w
import click
from splent_cli.utils.path_utils import PathUtils


@click.command(
    "feature:remove",
    short_help="Removes a local (non-versioned) feature from the active product.",
)
@click.argument("feature_name", required=True)
@click.option(
    "--namespace", "-n", help="Namespace (defaults to GITHUB_USER or 'splent-io')."
)
def feature_remove(feature_name, namespace):
    """
    Removes a local feature (no version, no repo) from the current SPLENT product:
    - Removes entry from [features] in pyproject.toml
    - Removes symlink under /workspace/<product>/features/<namespace>/<feature_name>
    """

    workspace = PathUtils.get_working_dir()
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    github_user = os.getenv("GITHUB_USER")
    org = namespace or github_user or "splent-io"
    org_safe = org.replace("-", "_")

    # --------------------------
    # 1️⃣ Update pyproject.toml
    # --------------------------
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product directory.")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    project = data.get("project", {})
    optional_deps = project.get("optional-dependencies", {})
    features = optional_deps.get("features", [])

    default_orgs = {"splent-io", github_user}
    entry_name = feature_name if org in default_orgs else f"{org_safe}/{feature_name}"

    if entry_name in features:
        features.remove(entry_name)
        data["project"]["optional-dependencies"]["features"] = features
        with open(pyproject_path, "wb") as f:
            tomli_w.dump(data, f)
        click.echo(f"🧩 Removed '{entry_name}' from pyproject.toml.")
    else:
        click.echo(f"ℹ️ Feature '{entry_name}' not found in pyproject.toml.")

    # --------------------------
    # 2️⃣ Remove symlink
    # --------------------------
    link_path = os.path.join(workspace, product, "features", org_safe, feature_name)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)
        click.echo(f"🗑️  Removed symlink: {link_path}")
    else:
        click.echo(f"ℹ️ Symlink not found: {link_path}")

    click.echo(
        f"✅ Feature '{entry_name}' removed successfully from product '{product}'."
    )
