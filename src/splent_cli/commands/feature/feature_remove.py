import os
import tomllib
import tomli_w
import click
from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    read_features_from_data,
    write_features_to_data,
)
from splent_cli.utils.manifest import (
    feature_key,
    remove_feature,
    get_dependents,
    get_feature_state,
)


@click.command(
    "feature:remove",
    short_help="Removes a local (non-versioned) feature from the active product.",
)
@click.argument("feature_name", required=True)
@click.option(
    "--namespace", "-n", help="Namespace (defaults to GITHUB_USER or 'splent-io')."
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip dependency and migration-state checks (use with care).",
)
def feature_remove(feature_name, namespace, force):
    """
    Removes a local feature (no version, no repo) from the current SPLENT product:
    - Removes entry from [features] in pyproject.toml
    - Removes symlink under /workspace/<product>/features/<namespace>/<feature_name>
    """

    product = context.require_app()
    workspace = str(context.workspace())

    # Parse namespace from argument if present (e.g. "splent-io/splent_feature_auth_2fa")
    if "/" in feature_name and not namespace:
        namespace, feature_name = feature_name.split("/", 1)

    org = namespace or "splent-io"
    org_safe = org.replace("-", "_").replace(".", "_")

    product_path = os.path.join(workspace, product)

    if not force:
        # --------------------------
        # Guard: dependency check
        # --------------------------
        dependents = get_dependents(product_path, feature_name)
        if dependents:
            click.secho(
                f"❌ Cannot remove '{feature_name}': the following installed features depend on it:\n"
                + "".join(f"   • {d}\n" for d in dependents)
                + "   Remove those features first, or use --force to bypass.",
                fg="red",
            )
            raise SystemExit(1)

        # --------------------------
        # Guard: migration state
        # Only block on "migrated" — "active" is set by the running app
        # and may be stale after a db:rollback. The rollback itself is
        # what matters, not the manifest state.
        # --------------------------
        key = feature_key(org_safe, feature_name)
        state = get_feature_state(product_path, key)
        if state == "migrated":
            click.secho(
                f"❌ Feature '{feature_name}' has migrations applied (state: {state}).\n"
                f"   Roll them back first:\n"
                f"   splent db:rollback {feature_name} --steps 999\n"
                f"   Or use --force to skip this check.",
                fg="red",
            )
            raise SystemExit(1)

    # --------------------------
    # 1️⃣ Update pyproject.toml
    # --------------------------
    pyproject_path = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product directory.")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = read_features_from_data(data)

    # Try multiple formats to match pyproject entry
    candidates = [
        feature_name,
        f"{org}/{feature_name}",
        f"{org_safe}/{feature_name}",
    ]
    entry_name = feature_name
    for candidate in candidates:
        if candidate in features:
            entry_name = candidate
            break

    if entry_name in features:
        features.remove(entry_name)
        write_features_to_data(data, features)
        with open(pyproject_path, "wb") as f:
            tomli_w.dump(data, f)
        click.echo(f"🧩 Removed '{entry_name}' from pyproject.toml.")
    else:
        click.echo(f"ℹ️ Feature '{entry_name}' not found in pyproject.toml.")

    # --------------------------
    # 2️⃣ Remove symlink
    # --------------------------
    link_path = os.path.join(product_path, "features", org_safe, feature_name)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)
        click.echo(f"🗑️  Removed symlink: {link_path}")
    else:
        click.echo(f"ℹ️ Symlink not found: {link_path}")

    # --------------------------
    # 3️⃣ Update manifest
    # --------------------------
    key = feature_key(org_safe, feature_name)
    remove_feature(product_path, product, key)

    click.echo(
        f"✅ Feature '{entry_name}' removed successfully from product '{product}'."
    )
