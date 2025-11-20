import os
import shutil
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
    "feature:delete",
    short_help="Delete a versioned feature from the SPLENT cache",
)
@click.argument("feature_identifier", required=True)
@click.argument("version", required=True)
@click.option("--force", is_flag=True, help="Delete without asking.")
def feature_delete(feature_identifier, version, force):
    """
    Delete a versioned feature from SPLENT's cache.

    - Scans ALL products under /workspace
    - Detects which products use this feature@version
    - Warns clearly if the deletion will break products
    - Asks for confirmation unless --force is provided
    """
    workspace = "/workspace"

    # --- Parse namespace + feature -----------------------------------------
    namespace, namespace_github, namespace_fs, feature_name = \
        parse_feature_identifier(feature_identifier)

    # Path to cached version
    cache_dir = os.path.join(
        workspace, ".splent_cache", "features", namespace_fs,
        f"{feature_name}@{version}"
    )

    if not os.path.exists(cache_dir):
        click.echo(f"❌ Feature version not found in cache: {cache_dir}")
        raise SystemExit(1)

    click.echo(f"🗂️  Cache directory: {cache_dir}")

    # --- Scan all products --------------------------------------------------
    used_by = []

    for item in os.listdir(workspace):
        product_path = os.path.join(workspace, item)
        pyproject_path = os.path.join(product_path, "pyproject.toml")

        if os.path.isfile(pyproject_path):
            try:
                with open(pyproject_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            if f"{feature_name}@{version}" in content:
                used_by.append(item)

    # --- Show which products use this feature ------------------------------
    if used_by:
        click.echo("\n🚨  The feature you are trying to delete is IN USE.\n")
        click.echo(f"Feature: {namespace_fs}/{feature_name}@{version}")
        click.echo("Used by the following products:\n")

        for p in used_by:
            click.echo(f"   • {p}")

        click.echo(
            "\n⚠️  Deleting this version will break these products until they:\n"
            "   - detach this version, or\n"
            "   - upgrade to another version.\n"
        )

        if not force:
            confirm = click.confirm(
                "Do you really want to delete it anyway?",
                default=False
            )
            if not confirm:
                click.echo("❌ Aborted.")
                raise SystemExit(1)
        else:
            click.echo("⚠️  --force supplied. Proceeding anyway.\n")

    else:
        click.echo("✅ No products depend on this version. Safe to delete.\n")

    # --- Delete -------------------------------------------------------------
    try:
        shutil.rmtree(cache_dir)
        click.echo(f"🧹 Deleted: {cache_dir}")
    except Exception as e:
        click.echo(f"❌ Failed to delete: {e}")
        raise SystemExit(1)

    click.echo("🎯 Feature version removed from cache.")
