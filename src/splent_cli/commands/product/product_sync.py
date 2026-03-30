import os
import tomllib
import shutil
import click
from splent_cli.services import context
from splent_cli.commands.feature.feature_clone import feature_clone
from splent_cli.utils.feature_utils import normalize_namespace, read_features_from_data
from splent_cli.utils.cache_utils import make_feature_writable


@click.command(
    "product:sync",
    short_help="Sync all versioned features declared in the active product.",
)
@click.pass_context
@click.option(
    "--force",
    is_flag=True,
    help="Force reclone each feature (delete its cache folder first).",
)
def product_sync(ctx, force):
    workspace = str(context.workspace())
    product = context.require_app()

    pyproject_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.secho(f"❌ pyproject.toml not found in product '{product}'", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = read_features_from_data(data, os.getenv("SPLENT_ENV"))

    if not features:
        click.secho("ℹ️ No features declared.", fg="yellow")
        return

    # Clean stale symlinks: remove any symlink not matching a declared feature
    features_dir = os.path.join(workspace, product, "features")
    _clean_stale_symlinks(features_dir, features)

    # Only remote features have a version
    remote_features = [f for f in features if "@" in f]
    local_features = [f for f in features if "@" not in f]

    if local_features:
        click.secho(
            f"🧱 Syncing {len(local_features)} local features (workspace root)...",
            fg="cyan",
        )
        for entry in local_features:
            if "/" in entry:
                ns_raw, name = entry.split("/", 1)
            else:
                ns_raw = "splent-io"
                name = entry
            ns_safe = normalize_namespace(ns_raw)

            feature_root = os.path.join(workspace, name)
            if not os.path.exists(feature_root):
                click.secho(
                    f"  ⚠️  {name} not found at workspace root — skipping.", fg="yellow"
                )
                continue

            product_features_dir = os.path.join(workspace, product, "features", ns_safe)
            link_path = os.path.join(product_features_dir, name)
            _create_symlink(feature_root, product_features_dir, link_path)

        click.echo()

    click.secho(f"🔄 Syncing {len(remote_features)} remote features...\n", fg="green")

    for entry in remote_features:
        # Parse namespace/repo@version
        if "/" in entry:
            namespace, rest = entry.split("/", 1)
        else:
            namespace = "splent-io"
            rest = entry

        repo, _, version = rest.partition("@")
        if not version:
            click.secho(f"⚠️  Skipping '{entry}': no version specified.", fg="yellow")
            continue

        namespace_safe = normalize_namespace(namespace)

        cache_dir = os.path.join(
            workspace, ".splent_cache", "features", namespace_safe, f"{repo}@{version}"
        )
        product_features_dir = os.path.join(
            workspace, product, "features", namespace_safe
        )
        link_path = os.path.join(product_features_dir, f"{repo}@{version}")

        # 1️⃣ FORCE → clear **only** this feature cache
        if os.path.exists(cache_dir) and force:
            click.secho(f"♻️ Removing cached feature: {cache_dir}", fg="yellow")
            make_feature_writable(cache_dir)  # unlock before deleting
            shutil.rmtree(cache_dir)

        # 2️⃣ Clone if missing
        if not os.path.exists(cache_dir):
            try:
                ctx.invoke(feature_clone, full_name=f"{namespace}/{repo}@{version}")
            except SystemExit as e:
                if e.code != 0:
                    click.secho(f"❌ Failed to clone {entry}", fg="red")
                    continue

        else:
            click.secho(f"✅ Using cached {namespace}/{repo}@{version}", fg="cyan")

        # 3️⃣ Create symlink
        _create_symlink(cache_dir, product_features_dir, link_path)

    # Clean up stale manifest entries and ensure all features are tracked
    from splent_cli.utils.manifest import cleanup_stale_entries, get_feature_state
    from splent_cli.utils.lifecycle import resolve_feature_key_from_entry, advance_state

    active_keys = set()
    all_entries = remote_features + local_features
    for entry in all_entries:
        key, _, _, _ = resolve_feature_key_from_entry(entry)
        active_keys.add(key)

    product_path = os.path.join(workspace, product)
    removed = cleanup_stale_entries(product_path, product, active_keys)
    if removed:
        click.secho(f"🧹 Cleaned {removed} stale manifest entries.", fg="yellow")

    # Ensure every active feature has at least a "declared" entry
    for entry in all_entries:
        key, ns, name, version = resolve_feature_key_from_entry(entry)
        if get_feature_state(product_path, key) is None:
            advance_state(
                product_path,
                product,
                key,
                to="declared",
                namespace=ns,
                name=name,
                version=version,
            )

    click.secho("\n✅ Product synced successfully.", fg="green")


def _clean_stale_symlinks(features_dir: str, declared: list[str]) -> None:
    """Remove symlinks in features/ that don't match any declared feature.

    This prevents accumulation of old version symlinks (e.g. @v1.2.5, @v1.2.7)
    when the pyproject now declares @v1.2.8.
    """
    if not os.path.isdir(features_dir):
        return

    # Build set of expected symlink names from declared features
    expected: set[str] = set()
    for entry in declared:
        if "/" in entry:
            ns_raw, rest = entry.split("/", 1)
        else:
            ns_raw, rest = "splent-io", entry
        ns_safe = normalize_namespace(ns_raw)

        name, _, version = rest.partition("@")
        if version:
            expected.add(f"{ns_safe}/{name}@{version}")
        else:
            expected.add(f"{ns_safe}/{name}")

    # Walk features/<org>/ and remove non-matching symlinks
    removed = 0
    for org_dir in os.listdir(features_dir):
        org_path = os.path.join(features_dir, org_dir)
        if not os.path.isdir(org_path):
            continue
        for entry in os.listdir(org_path):
            key = f"{org_dir}/{entry}"
            entry_path = os.path.join(org_path, entry)
            if os.path.islink(entry_path) and key not in expected:
                os.unlink(entry_path)
                removed += 1

    if removed:
        click.secho(f"🧹 Removed {removed} stale symlink(s) from features/.", fg="yellow")


def _create_symlink(cache_dir, product_features_dir, link_path):
    os.makedirs(product_features_dir, exist_ok=True)
    rel_target = os.path.relpath(cache_dir, product_features_dir)
    try:
        os.symlink(rel_target, link_path)
    except FileExistsError:
        os.unlink(link_path)
        os.symlink(rel_target, link_path)
    click.secho(f"🔗 Linked {link_path} → {rel_target}", fg="cyan")
