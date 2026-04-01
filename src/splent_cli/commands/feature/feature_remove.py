import os
import tomllib
import tomli_w
import click
from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    normalize_namespace,
    read_features_from_data,
    write_features_to_data,
    hot_uninstall,
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
    org_safe = normalize_namespace(org)

    product_path = os.path.join(workspace, product)
    short = feature_name.replace("splent_feature_", "")

    if not force:
        # Guard: dependency check
        dependents = get_dependents(product_path, feature_name)
        if dependents:
            click.secho(
                f"  Cannot remove '{short}': the following features depend on it:\n"
                + "".join(f"    - {d}\n" for d in dependents)
                + "  Remove those first, or use --force.",
                fg="red",
            )
            raise SystemExit(1)

        # Guard: migration state
        key = feature_key(org_safe, feature_name)
        state = get_feature_state(product_path, key)
        if state == "migrated":
            click.secho(
                f"  {short} has migrations applied (state: {state}).\n"
                f"  Roll them back first: splent db:rollback {feature_name} --steps 999\n"
                f"  Or use --force.",
                fg="red",
            )
            raise SystemExit(1)

    # ── Update pyproject.toml ─────────────────────────────────────────
    pyproject_path = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.secho("  pyproject.toml not found.", fg="red")
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
        click.echo(f"  {short} removed from pyproject.toml")
    else:
        click.echo(click.style(f"  {short} not found in pyproject.toml", dim=True))

    # ── Remove symlink ────────────────────────────────────────────────
    link_path = os.path.join(product_path, "features", org_safe, feature_name)
    if os.path.islink(link_path) or os.path.exists(link_path):
        os.unlink(link_path)

    # ── Update manifest ───────────────────────────────────────────────
    key = feature_key(org_safe, feature_name)
    remove_feature(product_path, product, key)

    # ── Hot uninstall from web container ──────────────────────────────
    hot_uninstall(product_path, feature_name)

    click.secho("  done.", fg="green")
