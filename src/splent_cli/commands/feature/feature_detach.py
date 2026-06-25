import os
import click
import tomli_w
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import (
    hot_uninstall,
    parse_feature_entry,
    write_features_to_data,
)
from splent_cli.utils.io_utils import load_toml, atomic_write
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
    data = load_toml(pyproject_path, what="pyproject.toml")

    changed = False
    for features_key in ("features", "features_dev", "features_prod"):
        features = data.get("tool", {}).get("splent", {}).get(features_key, [])
        if not features:
            continue

        updated = []
        for entry in features:
            _, name, entry_version = parse_feature_entry(entry)
            if name == feature_name and entry_version == version:
                # Drop the @version pin → revert to editable entry.
                ns_raw = entry.split("/", 1)[0] if "/" in entry else None
                updated.append(f"{ns_raw}/{name}" if ns_raw else name)
            else:
                updated.append(entry)

        if updated != features:
            write_features_to_data(data, updated, key=features_key)
            changed = True

    if changed:
        atomic_write(pyproject_path, tomli_w.dumps(data))

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
