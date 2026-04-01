import os
import tomllib
import tomli_w
import click
from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace, hot_reinstall
from splent_cli.utils.manifest import feature_key, set_feature_state


@click.command(
    "feature:add",
    short_help="Adds a local (non-versioned) feature to the active product.",
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
      splent feature:add drorganvidez/notepad
      splent feature:add splent-io/splent_feature_admin --dev
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

    # ── Update pyproject.toml ─────────────────────────────────────────
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.secho("  pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    from splent_cli.utils.feature_utils import (
        read_features_from_data,
        write_features_to_data,
    )

    features_key = f"features_{env_scope}" if env_scope else "features"
    features = (
        read_features_from_data(data)
        if not env_scope
        else (data.get("tool", {}).get("splent", {}).get(features_key, []))
    )

    short = feature_name.replace("splent_feature_", "")

    if full_name in features:
        click.echo(f"  {short} already in {features_key}.")
        return

    features.append(full_name)
    write_features_to_data(data, features, key=features_key)
    with open(pyproject_path, "wb") as f:
        tomli_w.dump(data, f)

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

    # ── Update manifest ───────────────────────────────────────────────
    product_path = os.path.join(workspace, product)
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
