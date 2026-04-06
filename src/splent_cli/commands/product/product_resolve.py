import os
import tomllib
import shutil
import click
from splent_cli.services import context
from splent_cli.commands.feature.feature_clone import feature_clone
from splent_cli.utils.feature_utils import (
    normalize_namespace,
    parse_feature_entry,
    read_features_from_data,
)
from splent_cli.utils.cache_utils import make_feature_writable


def _splent_list(data: dict, key: str) -> list[str]:
    """Read [tool.splent.<key>] as a list of strings."""
    raw = data.get("tool", {}).get("splent", {}).get(key)
    if not isinstance(raw, list):
        return []
    return [x.strip() for x in raw if isinstance(x, str) and x.strip()]


def _short_name(name: str) -> str:
    """Strip the ``splent_feature_`` prefix for display."""
    return (
        name[len("splent_feature_") :] if name.startswith("splent_feature_") else name
    )


def _feature_env_tag(entry: str, dev_set: set[str], prod_set: set[str]) -> str:
    """Return a coloured tag string if the feature is env-specific."""
    if entry in dev_set:
        return click.style("  dev", fg="yellow")
    if entry in prod_set:
        return click.style("  prod", fg="magenta")
    return ""


@click.command(
    "product:resolve",
    short_help="Sync all versioned features from GitHub into the local cache.",
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
        click.secho(f"pyproject.toml not found in product '{product}'", fg="red")
        raise SystemExit(1)

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        click.secho(f"❌ Invalid pyproject.toml: {e}", fg="red")
        click.secho(f"   File: {pyproject_path}", fg="red", dim=True)
        raise SystemExit(1)

    env = os.getenv("SPLENT_ENV")
    features = read_features_from_data(data, env)

    if not features:
        click.secho("No features declared.", fg="yellow")
        return

    # Build env-specific sets for tagging
    dev_set = set(_splent_list(data, "features_dev"))
    prod_set = set(_splent_list(data, "features_prod"))

    # Clean stale symlinks
    features_dir = os.path.join(workspace, product, "features")
    _clean_stale_symlinks(features_dir, features)

    # Separate local (editable) from remote (pinned)
    remote_features = [f for f in features if "@" in f]
    local_features = [f for f in features if "@" not in f]

    # ── Header ──────────────────────────────────────────────
    click.echo()
    click.secho("  product:resolve", fg="cyan", bold=True, nl=False)
    click.echo(f"  {product}")
    click.echo()

    # ── Editable features ──────────────────────────────────
    click.secho(f"  Editable features ({len(local_features)})", bold=True)
    if not local_features:
        click.secho("    (none)", dim=True)
    else:
        # Compute column width for alignment
        names = []
        for entry in local_features:
            _, name, _ = parse_feature_entry(entry)
            names.append((_short_name(name), entry, name))
        max_w = max(len(n[0]) for n in names)

        for short, entry, name in names:
            ns_safe, _, _ = parse_feature_entry(entry)
            feature_root = os.path.join(workspace, name)
            product_features_dir = os.path.join(workspace, product, "features", ns_safe)
            link_path = os.path.join(product_features_dir, name)

            if not os.path.exists(feature_root):
                marker = click.style("  ✗", fg="red")
                label = click.style(f"  {short:<{max_w}}", fg="red")
                tag = click.style("  not found", fg="red", dim=True)
                click.echo(f"  {marker}{label}{tag}")
                continue

            _create_symlink(feature_root, product_features_dir, link_path)
            marker = click.style("  ✓", fg="green")
            label = f"  {short:<{max_w}}"
            loc = click.style("  workspace root", dim=True)
            tag = _feature_env_tag(entry, dev_set, prod_set)
            click.echo(f"  {marker}{label}{loc}{tag}")

    click.echo()

    # ── Pinned features ────────────────────────────────────
    click.secho(f"  Pinned features ({len(remote_features)})", bold=True)
    if not remote_features:
        click.secho("    (none)", dim=True)
    else:
        names_r = []
        for entry in remote_features:
            ns_safe, repo, version = parse_feature_entry(entry)
            # Extract raw namespace (e.g. "splent-io") for Git URLs;
            # ns_safe ("splent_io") is only for filesystem paths.
            ns_raw = entry.split("/", 1)[0] if "/" in entry else "splent-io"
            names_r.append((_short_name(repo), entry, ns_safe, ns_raw, repo, version))
        max_w_r = max(len(n[0]) for n in names_r)

        for short, entry, ns_safe, ns_raw, repo, version in names_r:
            cache_dir = os.path.join(
                workspace, ".splent_cache", "features", ns_safe, f"{repo}@{version}"
            )
            product_features_dir = os.path.join(workspace, product, "features", ns_safe)
            link_path = os.path.join(product_features_dir, f"{repo}@{version}")

            # Force reclone
            if os.path.exists(cache_dir) and force:
                make_feature_writable(cache_dir)
                shutil.rmtree(cache_dir)

            # Clone if missing
            cloned = False
            if not os.path.exists(cache_dir):
                try:
                    ctx.invoke(feature_clone, full_name=f"{ns_raw}/{repo}@{version}")
                    cloned = True
                except SystemExit as e:
                    if e.code != 0:
                        marker = click.style("  ✗", fg="red")
                        label = click.style(f"  {short:<{max_w_r}}", fg="red")
                        ver = click.style(f"  {version}", fg="red", dim=True)
                        click.echo(f"  {marker}{label}{ver}  clone failed")
                        continue

            _create_symlink(cache_dir, product_features_dir, link_path)
            marker = click.style("  ✓", fg="green")
            label = f"  {short:<{max_w_r}}"
            ver = click.style(f"  {version}", dim=True)
            src = click.style("  cached" if not cloned else "  cloned", dim=True)
            tag = _feature_env_tag(entry, dev_set, prod_set)
            click.echo(f"  {marker}{label}{ver}{src}{tag}")

    click.echo()

    # ── Manifest cleanup ───────────────────────────────────
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
        click.secho(f"  Cleaned {removed} stale manifest entries.", fg="yellow")

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

    total = len(local_features) + len(remote_features)
    click.secho(f"  Synced {total} features.", fg="green", bold=True)
    click.echo()


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
        click.secho(f"  Removed {removed} stale symlink(s).", fg="yellow")


def _create_symlink(cache_dir, product_features_dir, link_path):
    os.makedirs(product_features_dir, exist_ok=True)
    rel_target = os.path.relpath(cache_dir, product_features_dir)
    try:
        os.symlink(rel_target, link_path)
    except FileExistsError:
        os.unlink(link_path)
        os.symlink(rel_target, link_path)
