import os
import tomli_w
import click
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import (
    drop_feature_entries,
    find_feature_entries,
    hot_reinstall,
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
        # Asking to attach a version means wanting it, so fetch it rather
        # than printing the command that fetches it. Attaching after a
        # release, on a machine that has never seen that version, is the
        # normal case and not an error.
        from splent_cli.commands.feature.feature_clone import feature_clone

        click.secho(
            f"  {feature_name}@{version} is not in the cache yet, fetching it.",
            fg="bright_black",
        )
        # The callback, not the command: this is not a click context and
        # feature_attach is itself invoked from the release pipeline.
        feature_clone.callback(full_name=f"{namespace}/{feature_name}@{version}")
        if not os.path.exists(versioned_dir):
            click.secho(
                f"  {feature_name}@{version} could not be fetched, so there is "
                "nothing to attach.",
                fg="red",
            )
            raise SystemExit(1)

    short = feature_name.replace("splent_feature_", "")

    # ── Auto-detect env scope from feature contract ───────────────────
    if not env_scope:
        feat_pyproject = os.path.join(versioned_dir, "pyproject.toml")
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
    data = load_toml(pyproject_path, what="pyproject.toml")

    # Keep one spelling of the namespace per product, whichever it already
    # uses, instead of whichever this invocation happened to be typed with.
    spelling = product_namespace_spelling(data, namespace_fs, namespace)
    full_name = f"{spelling}/{feature_name}@{version}"

    features_key = f"features_{env_scope}" if env_scope else "features"

    # Every previous declaration of this feature, wherever it lives and however
    # its namespace is spelled (splent-io and splent_io are the same namespace).
    declared = find_feature_entries(data, feature_name, namespace=namespace_fs)
    stale = [pair for pair in declared if pair != (features_key, full_name)]

    if declared and not stale:
        click.echo(f"  {short}@{version} already in {features_key}.")
    else:
        # Drop the stale declarations (other list, other version, other spelling
        # of the namespace) together with what each one created, so the feature
        # ends up declared exactly once and with a single symlink.
        for stale_key, stale_entry in stale:
            _, stale_name, stale_version = parse_feature_entry(stale_entry)
            remove_feature_link(product_path, namespace_fs, stale_name, stale_version)
            remove_feature(
                product_path,
                product,
                feature_key(namespace_fs, stale_name, stale_version),
            )
            click.echo(
                click.style(f"  replacing {stale_entry} in {stale_key}", dim=True)
            )

        drop_feature_entries(data, feature_name, namespace=namespace_fs)
        features = read_feature_list(data, features_key)
        features.append(full_name)
        write_features_to_data(data, features, key=features_key)
        atomic_write(pyproject_path, tomli_w.dumps(data))
        scope_label = f" ({env_scope} only)" if env_scope else ""
        click.echo(f"  {short}@{version} attached{scope_label}")

    # ── Create/update symlink ─────────────────────────────────────────
    product_features_dir = os.path.join(product_path, "features", namespace_fs)
    os.makedirs(product_features_dir, exist_ok=True)

    leaf = f"{feature_name}@{version}"
    new_link = os.path.join(product_features_dir, leaf)
    if os.path.islink(new_link):
        os.unlink(new_link)
    rel_target = os.path.relpath(versioned_dir, product_features_dir)
    os.symlink(rel_target, new_link)

    for gone in prune_feature_links(
        product_path, namespace_fs, feature_name, keep=leaf
    ):
        click.echo(click.style(f"  removing leftover link {gone}", dim=True))

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
