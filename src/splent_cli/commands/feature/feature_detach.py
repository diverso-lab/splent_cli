import os
import re
import click
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import hot_uninstall
from splent_cli.utils.manifest import (
    feature_key,
    remove_feature,
    get_dependents,
    get_feature_state,
)


@click.command(
    "feature:detach",
    short_help="Unregister a versioned feature from the active product (cache is kept).",
)
@click.argument("feature_identifier", required=True)
@click.argument("version", required=True)
@click.option(
    "--force",
    is_flag=True,
    help="Skip dependency and migration-state checks (use with care).",
)
def feature_detach(feature_identifier, version, force):
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
    short = feature_name.replace("splent_feature_", "")

    if not os.path.exists(pyproject_path):
        click.secho("  pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    if not force:
        # Guard: dependency check
        dependents = get_dependents(product_path, feature_name)
        if dependents:
            click.secho(
                f"  Cannot detach '{short}': the following features depend on it:\n"
                + "".join(f"    - {d}\n" for d in dependents)
                + "  Remove those first, or use --force.",
                fg="red",
            )
            raise SystemExit(1)

        # Guard: migration state
        key = feature_key(namespace_fs, feature_name, version)
        state = get_feature_state(product_path, key)
        if state in ("migrated", "active"):
            click.secho(
                f"  {short} has migrations applied (state: {state}).\n"
                f"  Roll them back first: splent db:rollback {feature_name} --steps 999\n"
                f"  Or use --force.",
                fg="red",
            )
            raise SystemExit(1)

    # ── Remove versioned reference from pyproject ─────────────────────
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    pattern = re.escape(f"{feature_name}@{version}")
    new_content = re.sub(pattern, feature_name, content)

    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    click.echo(f"  {short}@{version} removed from pyproject.toml")

    # ── Remove symlink ────────────────────────────────────────────────
    product_features_dir = os.path.join(product_path, "features", namespace_fs)
    link_path = os.path.join(product_features_dir, f"{feature_name}@{version}")

    if os.path.islink(link_path):
        os.unlink(link_path)

    # ── Update manifest ───────────────────────────────────────────────
    key = feature_key(namespace_fs, feature_name, version)
    remove_feature(str(ws / product), product, key)

    # ── Hot uninstall from web container ──────────────────────────────
    hot_uninstall(product_path, feature_name)

    click.secho("  done.", fg="green")
