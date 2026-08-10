import os
import tomli_w
import click
from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    drop_feature_entries,
    find_feature_entries,
    hot_reinstall,
    normalize_namespace,
    parse_feature_entry,
    product_namespace_spelling,
    prune_feature_links,
    read_feature_list,
    remove_feature_link,
    write_features_to_data,
)
from splent_cli.utils.io_utils import load_toml, atomic_write
from splent_cli.utils.manifest import feature_key, remove_feature, set_feature_state


@click.command(
    "feature:add",
    short_help="Register a local editable feature in the active product.",
)
@click.argument("full_name", required=True)
@click.option(
    "--dev",
    "env_scope",
    flag_value="dev",
    help="Add to features_dev (development only).",
)
@click.option(
    "--prod",
    "env_scope",
    flag_value="prod",
    help="Add to features_prod (production only).",
)
def feature_add(full_name, env_scope):
    """
    Adds a local feature (no version, no repo) to the current SPLENT product.
    The feature name must be in the format <namespace>/<feature_name>.

    \b
    By default, adds to [tool.splent].features (all environments).
    Use --dev or --prod to add to features_dev or features_prod.

    \b
    Examples:
      splent feature:add splent-io/splent_feature_admin
      splent feature:add drorganvidez/notepad --dev
    """

    if "/" not in full_name:
        click.secho("  Invalid format. Use: <namespace>/<feature_name>", fg="red")
        raise SystemExit(1)

    namespace, feature_name = full_name.split("/", 1)
    org_safe = normalize_namespace(namespace)

    workspace = str(context.workspace())
    product = context.require_app()

    # Editable features live at workspace root
    feature_dir = os.path.join(workspace, feature_name)
    if not os.path.exists(feature_dir):
        click.secho(f"  {feature_name} not found at workspace root.", fg="red")
        click.echo(
            click.style("  create it first: ", dim=True)
            + f"splent feature:create {full_name}"
        )
        raise SystemExit(1)

    # ── Auto-detect env scope from feature contract ───────────────────
    if not env_scope:
        feat_pyproject = os.path.join(feature_dir, "pyproject.toml")
        if os.path.isfile(feat_pyproject):
            feat_data = load_toml(feat_pyproject, what="pyproject.toml")
            contract_env = (
                feat_data.get("tool", {})
                .get("splent", {})
                .get("contract", {})
                .get("env")
            )
            if contract_env:
                env_scope = contract_env
                click.echo(
                    click.style("  scope    ", dim=True)
                    + f"contract declares env={contract_env} → features_{contract_env}"
                )

    # ── Update pyproject.toml ─────────────────────────────────────────
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.secho("  pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    data = load_toml(pyproject_path, what="pyproject.toml")

    # Keep one spelling of the namespace per product, whichever it already
    # uses, instead of whichever this invocation happened to be typed with.
    full_name = (
        f"{product_namespace_spelling(data, org_safe, namespace)}/{feature_name}"
    )

    features_key = f"features_{env_scope}" if env_scope else "features"
    short = feature_name.replace("splent_feature_", "")

    product_path = os.path.join(workspace, product)

    # Every previous declaration of this feature, wherever it lives and however
    # its namespace is spelled (splent-io and splent_io are the same namespace).
    declared = find_feature_entries(data, feature_name, namespace=org_safe)
    stale = [pair for pair in declared if pair != (features_key, full_name)]

    if declared and not stale:
        click.echo(f"  {short} already in {features_key}.")
        return

    # Drop the stale declarations (other list, pinned version, other spelling of
    # the namespace) together with what each one created, so the feature ends up
    # declared exactly once and with a single symlink.
    for stale_key, stale_entry in stale:
        _, stale_name, stale_version = parse_feature_entry(stale_entry)
        remove_feature_link(product_path, org_safe, stale_name, stale_version)
        remove_feature(
            product_path, product, feature_key(org_safe, stale_name, stale_version)
        )
        click.echo(click.style(f"  replacing {stale_entry} in {stale_key}", dim=True))

    drop_feature_entries(data, feature_name, namespace=org_safe)
    features = read_feature_list(data, features_key)
    features.append(full_name)
    write_features_to_data(data, features, key=features_key)
    atomic_write(pyproject_path, tomli_w.dumps(data))

    scope_label = f" ({env_scope} only)" if env_scope else ""
    click.echo(f"  {short} added to {features_key}{scope_label}")

    # ── Create symlink ────────────────────────────────────────────────
    product_features_dir = os.path.join(workspace, product, "features", org_safe)
    os.makedirs(product_features_dir, exist_ok=True)

    link_path = os.path.join(product_features_dir, feature_name)
    rel_target = os.path.relpath(feature_dir, product_features_dir)
    try:
        os.symlink(rel_target, link_path)
    except FileExistsError:
        os.unlink(link_path)
        os.symlink(rel_target, link_path)

    for gone in prune_feature_links(
        product_path, org_safe, feature_name, keep=feature_name
    ):
        click.echo(click.style(f"  removing leftover link {gone}", dim=True))

    # ── Update manifest ───────────────────────────────────────────────
    key = feature_key(namespace, feature_name)
    set_feature_state(
        product_path,
        product,
        key,
        "declared",
        namespace=namespace,
        name=feature_name,
        version=None,
        mode="editable",
    )

    # ── Hot reinstall in web container ────────────────────────────────
    hot_reinstall(product_path, f"/workspace/{feature_name}", feature_name)

    click.secho("  done.", fg="green")
