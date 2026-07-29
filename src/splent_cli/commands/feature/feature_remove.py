import os
import tomli_w
import click
from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    FEATURE_LIST_KEYS,
    drop_feature_entries,
    find_feature_entries,
    normalize_namespace,
    parse_feature_entry,
    remove_feature_link,
    hot_uninstall,
)
from splent_cli.utils.io_utils import load_toml, atomic_write
from splent_cli.utils.manifest import (
    feature_key,
    remove_feature,
    get_dependents,
    get_feature_state,
)


@click.command(
    "feature:remove",
    short_help="Unregister an editable feature from the active product (keeps files).",
)
@click.argument("feature_name", required=True)
@click.option(
    "--namespace", "-n", help="Namespace (defaults to GITHUB_USER or 'splent-io')."
)
@click.option(
    "--dev",
    "env_scope",
    flag_value="dev",
    help="Only remove from features_dev (development only).",
)
@click.option(
    "--prod",
    "env_scope",
    flag_value="prod",
    help="Only remove from features_prod (production only).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip dependency and migration-state checks (use with care).",
)
def feature_remove(feature_name, namespace, env_scope, force):
    """
    Removes a local feature (no version, no repo) from the current SPLENT product:
    - Removes entry from [features] in pyproject.toml
    - Removes symlink under /workspace/<product>/features/<namespace>/<feature_name>

    \b
    Without flags every list is searched (features, features_dev, features_prod).
    Use --dev or --prod to restrict the removal to one of them.
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

    data = load_toml(pyproject_path, what="pyproject.toml")

    # Search the list the flags point at, or all of them when there is no flag,
    # so a feature declared only in features_dev can still be removed.  Matching
    # normalizes the namespace spelling (splent-io == splent_io) and ignores any
    # pinned version, so every spelling of the entry is caught.
    keys = (f"features_{env_scope}",) if env_scope else FEATURE_LIST_KEYS
    removed = drop_feature_entries(data, feature_name, namespace=org_safe, keys=keys)
    # What survives the removal, e.g. a features entry when --dev was used.
    remaining = find_feature_entries(data, feature_name, namespace=org_safe)

    if removed:
        atomic_write(pyproject_path, tomli_w.dumps(data))
        for features_key, _ in removed:
            click.echo(f"  {short} removed from {features_key}")
    elif remaining:
        # Restricted by --dev/--prod but declared in another list.
        lists = ", ".join(sorted({k for k, _ in remaining}))
        click.secho(
            f"  {short} is not in features_{env_scope}, it is declared in {lists}.",
            fg="yellow",
        )
    else:
        click.echo(click.style(f"  {short} not found in pyproject.toml", dim=True))

    # ── Remove symlinks and manifest entries ──────────────────────────
    # One per removed declaration, unless a surviving declaration points at the
    # same symlink (the same entry may be listed in features and features_dev).
    kept = {parse_feature_entry(entry)[1:] for _, entry in remaining}
    for _, entry in removed:
        _, entry_name, entry_version = parse_feature_entry(entry)
        if (entry_name, entry_version) in kept:
            continue
        remove_feature_link(product_path, org_safe, entry_name, entry_version)
        remove_feature(
            product_path, product, feature_key(org_safe, entry_name, entry_version)
        )

    if remaining:
        click.secho("  done.", fg="green")
        return

    # Nothing left: clean up any leftover editable symlink and manifest entry.
    remove_feature_link(product_path, org_safe, feature_name)
    remove_feature(product_path, product, feature_key(org_safe, feature_name))

    # ── Hot uninstall from web container ──────────────────────────────
    hot_uninstall(product_path, feature_name)

    click.secho("  done.", fg="green")
