import os
import tomllib
import tomli_w
import click
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import hot_reinstall
from splent_cli.utils.manifest import feature_key, set_feature_state


@click.command(
    "feature:attach",
    short_help="Register a cached versioned feature in the active product.",
)
@click.argument("feature_identifier", required=True)
@click.argument("version", required=True)
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
def feature_attach(feature_identifier, version, env_scope):
    """
    Attach a cached feature version to the current product.

    - Requires the feature to already be in the local cache.
      If not, run: splent feature:clone <namespace>/<feature>@<version>
    - Updates pyproject.toml referencing feature@version.
    - Creates/updates the versioned symlink in features/<namespace>/.
    - Reinstalls the feature in the web container for hot reload.
    """
    product = context.require_app()
    ws = context.workspace()

    # --- Parse feature identifier -------------------------------------------
    namespace, namespace_github, namespace_fs, feature_name = (
        compose.parse_feature_identifier(feature_identifier)
    )

    cache_base = str(ws / ".splent_cache" / "features" / namespace_fs)
    product_path = str(ws / product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.secho("  pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    # ── Verify feature exists in cache ────────────────────────────────
    versioned_dir = os.path.join(cache_base, f"{feature_name}@{version}")

    if not os.path.exists(versioned_dir):
        click.secho(f"  {feature_name}@{version} not found in cache.", fg="red")
        click.echo(
            click.style("  clone it first: ", dim=True)
            + f"splent feature:clone {namespace}/{feature_name}@{version}"
        )
        raise SystemExit(1)

    short = feature_name.replace("splent_feature_", "")

    # ── Auto-detect env scope from feature contract ───────────────────
    if not env_scope:
        feat_pyproject = os.path.join(versioned_dir, "pyproject.toml")
        if os.path.isfile(feat_pyproject):
            import tomllib as _tomllib

            with open(feat_pyproject, "rb") as f:
                feat_data = _tomllib.load(f)
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
    full_name = f"{namespace}/{feature_name}@{version}"
    bare_name = f"{namespace}/{feature_name}"

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

    if full_name in features:
        click.echo(f"  {short}@{version} already in {features_key}.")
    else:
        # Replace bare entry or old versioned entry if present
        features = [
            f for f in features if f != bare_name and not f.startswith(f"{bare_name}@")
        ]
        features.append(full_name)
        write_features_to_data(data, features, key=features_key)
        with open(pyproject_path, "wb") as f:
            tomli_w.dump(data, f)
        scope_label = f" ({env_scope} only)" if env_scope else ""
        click.echo(f"  {short}@{version} attached{scope_label}")

    # ── Create/update symlink ─────────────────────────────────────────
    product_features_dir = os.path.join(product_path, "features", namespace_fs)
    os.makedirs(product_features_dir, exist_ok=True)

    new_link = os.path.join(product_features_dir, f"{feature_name}@{version}")
    if os.path.islink(new_link):
        os.unlink(new_link)
    rel_target = os.path.relpath(versioned_dir, product_features_dir)
    os.symlink(rel_target, new_link)

    # ── Update manifest ───────────────────────────────────────────────
    key = feature_key(namespace_fs, feature_name, version)
    set_feature_state(
        product_path,
        product,
        key,
        "declared",
        namespace=namespace_fs,
        name=feature_name,
        version=version,
        mode="pinned",
    )

    # ── Hot reinstall in web container ────────────────────────────────
    # Symlink resolves to cache path — install from there
    install_path = (
        f"/workspace/{product}/features/{namespace_fs}/{feature_name}@{version}"
    )
    hot_reinstall(product_path, install_path, feature_name)

    click.secho("  done.", fg="green")
