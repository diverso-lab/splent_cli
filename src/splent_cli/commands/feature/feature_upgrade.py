import os
import re
import subprocess
import click
from pathlib import Path
from packaging.version import Version, InvalidVersion
from splent_cli.services import context


def _latest_cached_version(ns_dir: Path, name: str) -> str | None:
    """Return the latest version string cached for a given feature name."""
    versions = []
    for d in ns_dir.iterdir():
        if d.is_dir() and d.name.startswith(f"{name}@"):
            v = d.name.split("@", 1)[1]
            versions.append(v)
    if not versions:
        return None

    def sort_key(v):
        try:
            return Version(v.lstrip("v"))
        except InvalidVersion:
            return Version("0")

    return max(versions, key=sort_key)


def _get_product_feature_versions(pyproject_path: Path) -> dict:
    """Returns {name: current_version_or_None} from the product's pyproject."""
    import tomllib
    if not pyproject_path.exists():
        return {}
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        refs = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
        result = {}
        for ref in refs:
            base = ref.split("/", 1)[1] if "/" in ref else ref
            if "@" in base:
                name, ver = base.split("@", 1)
            else:
                name, ver = base, None
            result[name] = ver
        return result
    except Exception:
        return {}


def _update_pyproject(pyproject_path: Path, name: str, old_ver: str, new_ver: str):
    content = pyproject_path.read_text()
    if old_ver:
        new_content = content.replace(f"{name}@{old_ver}", f"{name}@{new_ver}")
    else:
        # editable → add version
        new_content = re.sub(
            rf'(["\'](?:[^/\"\']+/)?){re.escape(name)}(["\'])',
            rf'\g<1>{name}@{new_ver}\g<2>',
            content,
        )
    pyproject_path.write_text(new_content)


def _update_symlink(product_path: Path, ns_fs: str, name: str, old_ver: str | None, new_ver: str, cache_root: Path):
    features_dir = product_path / "features" / ns_fs
    features_dir.mkdir(parents=True, exist_ok=True)

    if old_ver:
        old_link = features_dir / f"{name}@{old_ver}"
        if old_link.is_symlink():
            old_link.unlink()

    new_link = features_dir / f"{name}@{new_ver}"
    target = cache_root / ns_fs / f"{name}@{new_ver}"
    if new_link.is_symlink():
        new_link.unlink()
    new_link.symlink_to(target)


def _clone_if_missing(ns_fs: str, name: str, version: str, cache_root: Path):
    target = cache_root / ns_fs / f"{name}@{version}"
    if target.exists():
        return
    ns_github = ns_fs.replace("_", "-")
    use_ssh = os.getenv("SPLENT_USE_SSH", "").lower() == "true"
    url = (
        f"git@github.com:{ns_github}/{name}.git"
        if use_ssh
        else f"https://github.com/{ns_github}/{name}.git"
    )
    click.echo(f"  ⬇️  Cloning {ns_fs}/{name}@{version}...")
    subprocess.run(
        ["git", "clone", "--branch", version, "--depth", "1", url, str(target)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


@click.command("feature:upgrade", short_help="Upgrade declared features to the latest cached version.")
@click.argument("feature_ref", required=False)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def feature_upgrade(feature_ref, yes):
    """
    Upgrade one or all features declared in the active product to the latest version
    available in the local cache.

    \b
    With no arguments, checks all declared features.
    With FEATURE_REF (e.g. splent_feature_auth), upgrades only that one.

    Pairs naturally with: splent cache:outdated
    """
    workspace = context.workspace()
    product = context.require_app()

    product_path = workspace / product
    pyproject_path = product_path / "pyproject.toml"
    cache_root = workspace / ".splent_cache" / "features"

    declared = _get_product_feature_versions(pyproject_path)
    if not declared:
        click.secho("ℹ️  No features declared in pyproject.toml.", fg="yellow")
        return

    if feature_ref:
        name = feature_ref.split("/", 1)[1] if "/" in feature_ref else feature_ref
        if name not in declared:
            click.secho(f"⚠️  '{name}' not declared in this product.", fg="yellow")
            return
        targets = {name: declared[name]}
    else:
        targets = declared

    upgrades = []
    for name, current_ver in targets.items():
        # Find namespace in cache
        ns_dir = None
        ns_fs = None
        for ns in cache_root.iterdir():
            if ns.is_dir() and (ns / name).exists() or any(
                d.name.startswith(f"{name}@") for d in ns.iterdir() if d.is_dir()
            ):
                ns_dir = ns
                ns_fs = ns.name
                break

        if not ns_dir:
            continue

        latest = _latest_cached_version(ns_dir, name)
        if not latest:
            continue

        if current_ver is None:
            upgrades.append((ns_fs, name, current_ver, latest))
            continue

        try:
            is_newer = Version(latest.lstrip("v")) > Version(current_ver.lstrip("v"))
        except InvalidVersion:
            is_newer = latest != current_ver

        if is_newer:
            upgrades.append((ns_fs, name, current_ver, latest))

    if not upgrades:
        click.secho("✅ All features are already at the latest cached version.", fg="green")
        return

    click.secho(f"Features to upgrade ({len(upgrades)}):\n", fg="cyan")
    for ns_fs, name, cur, new in upgrades:
        cur_label = click.style(cur or "editable", fg="red")
        new_label = click.style(new, fg="green")
        click.echo(f"  {ns_fs}/{name}  {cur_label} → {new_label}")

    click.echo()
    if not yes and not click.confirm("Proceed with upgrade?"):
        click.echo("❎ Cancelled.")
        raise SystemExit(0)

    for ns_fs, name, cur, new in upgrades:
        try:
            _clone_if_missing(ns_fs, name, new, cache_root)
            _update_pyproject(pyproject_path, name, cur, new)
            _update_symlink(product_path, ns_fs, name, cur, new, cache_root)
            click.secho(f"  ✔ {ns_fs}/{name} → {new}", fg="green")
        except Exception as e:
            click.secho(f"  ✖ {ns_fs}/{name}: {e}", fg="red")

    click.echo()
    click.secho("Done. Run 'splent product:sync' if any feature was missing from cache.", fg="cyan")


cli_command = feature_upgrade
