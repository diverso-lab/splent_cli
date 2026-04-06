from splent_cli.services import context
import re
import click
from pathlib import Path
from collections import defaultdict
from packaging.version import Version, InvalidVersion


def _get_cache_versions(cache_root: Path) -> dict:
    """Returns {name: [version, ...]} with all versioned snapshots in cache."""
    versions = defaultdict(list)
    if not cache_root.exists():
        return versions
    for ns_dir in cache_root.iterdir():
        if not ns_dir.is_dir():
            continue
        for feat_dir in ns_dir.iterdir():
            if not feat_dir.is_dir() or "@" not in feat_dir.name:
                continue
            name, version = feat_dir.name.split("@", 1)
            versions[name].append(version)
    return versions


def _latest(versions: list) -> str:
    """Returns the latest version string, using semver if possible."""

    def sort_key(v):
        try:
            return Version(v.lstrip("v"))
        except InvalidVersion:
            return Version("0")

    return max(versions, key=sort_key)


def _get_product_features(workspace: Path) -> dict:
    """Returns {product_name: {feature_name: version_or_None}}."""
    products = {}
    for product_dir in sorted(workspace.iterdir()):
        if not product_dir.is_dir() or product_dir.name.startswith("."):
            continue
        pyproject = product_dir / "pyproject.toml"
        if not pyproject.exists():
            continue
        content = pyproject.read_text()
        m = re.search(
            r"\[project\.optional-dependencies\].*?features\s*=\s*\[(.*?)\]",
            content,
            re.DOTALL,
        )
        if not m:
            continue
        features = {}
        for raw in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1)):
            ref = raw[0] or raw[1]
            if "/" in ref:
                ref = ref.split("/", 1)[1]
            if "@" in ref:
                name, version = ref.split("@", 1)
                features[name] = version
            else:
                features[ref] = None
        if features:
            products[product_dir.name] = features
    return products


@click.command(
    "cache:outdated",
    short_help="Show products using older feature versions than what is in cache.",
)
def cache_outdated():
    """
    Compares the version each product uses against all versions available in cache.
    Reports features where a newer version exists locally.
    """
    workspace = context.workspace()
    cache_root = workspace / ".splent_cache" / "features"

    cache_versions = _get_cache_versions(cache_root)
    if not cache_versions:
        click.secho("ℹ️  No versioned snapshots in cache.", fg="yellow")
        return

    products = _get_product_features(workspace)
    if not products:
        click.secho("ℹ️  No products with declared features found.", fg="yellow")
        return

    outdated = []
    for product, features in sorted(products.items()):
        for name, current_ver in features.items():
            available = cache_versions.get(name)
            if not available:
                continue
            if current_ver is None:
                # Editable — show available versions as informational
                outdated.append(
                    (product, name, "(editable)", _latest(available), available)
                )
                continue
            latest = _latest(available)
            try:
                is_old = Version(latest.lstrip("v")) > Version(current_ver.lstrip("v"))
            except InvalidVersion:
                is_old = latest != current_ver
            if is_old:
                outdated.append((product, name, current_ver, latest, available))

    if not outdated:
        click.secho("✅ All products are on the latest cached version.", fg="green")
        return

    click.secho(f"Outdated features ({len(outdated)}):\n", fg="yellow")
    for product, name, current, latest, available in outdated:
        click.echo(
            f"  {click.style(product, bold=True)}  {name}  "
            f"{click.style(current, fg='red')} → {click.style(latest, fg='green')}"
        )
        others = sorted(set(available) - {latest})
        if others:
            click.echo(f"    also cached: {', '.join(others)}")
    click.echo()


cli_command = cache_outdated
