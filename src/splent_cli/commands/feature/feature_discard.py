import os
import click
import shutil
import tomllib

DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


def _get_namespace_safe(ns: str):
    return ns.replace("-", "_")


def _find_products_using_editable(workspace, feature_name, ns_safe):
    """Scan all products in workspace and return the ones using the editable feature."""
    used_in = []

    for item in os.listdir(workspace):
        product_path = os.path.join(workspace, item)
        if not os.path.isdir(product_path):
            continue

        pyproject_path = os.path.join(product_path, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            continue

        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            continue

        features = (
            data.get("project", {})
            .get("optional-dependencies", {})
            .get("features", [])
        )

        # Editable feature = aparece SIN @versión
        if feature_name in features:
            used_in.append(item)

    return used_in


@click.command(
    "feature:discard",
    short_help="Discard the editable version of a feature.",
)
@click.argument("feature_name", required=True)
@click.option("--namespace", default=DEFAULT_NAMESPACE, help="Feature namespace.")
def feature_discard(feature_name, namespace):
    """
    Delete the editable version of a feature:

      - Ensures that no product depends on it in editable mode.
      - Removes ONLY the editable folder, never the versioned cache.

    This is a safe operation: versioned snapshots remain untouched.
    """

    workspace = "/workspace"
    ns_safe = _get_namespace_safe(namespace)

    editable_path = os.path.join(
        workspace, ".splent_cache", "features", ns_safe, feature_name
    )

    # 1) Check if editable exists
    if not os.path.exists(editable_path):
        click.echo(f"ℹ️ No editable folder found for {feature_name}. Nothing to discard.")
        raise SystemExit(0)

    click.echo(f"🧩 Editable feature detected at:\n   {editable_path}")

    # 2) Detect products using this editable feature
    used_in = _find_products_using_editable(workspace, feature_name, ns_safe)

    if used_in:
        click.echo("⚠️ This editable feature is currently being used in:")
        for product in used_in:
            click.echo(f"   - {product}")

        if not click.confirm("Do you want to continue and break these products?", default=False):
            click.echo("🚫 Aborted. Nothing removed.")
            raise SystemExit(1)

    # 3) Delete editable folder
    click.echo("🗑️ Removing editable feature folder...")
    shutil.rmtree(editable_path)

    click.echo("✅ Editable feature removed.")
    click.echo("🎯 All versioned snapshots preserved.")
