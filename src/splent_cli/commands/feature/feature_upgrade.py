import json
import os
import subprocess
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import click
from packaging.version import Version, InvalidVersion

from splent_cli.services import context
from splent_cli.utils.feature_utils import read_features_from_data


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _github_headers(token: str | None) -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "splent-cli",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _latest_remote_version(org: str, repo: str, token: str | None) -> str | None:
    """Return the latest tag from GitHub, or None if the repo has no tags."""
    headers = _github_headers(token)
    url = f"https://api.github.com/repos/{org}/{repo}/tags?per_page=1&page=1"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            batch = json.loads(resp.read().decode())
        return batch[0]["name"] if batch else None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except urllib.error.URLError as e:
        click.secho(f"  ⚠  Network error fetching {repo}: {e.reason}", fg="yellow")
        return None


# ── Pyproject helpers ─────────────────────────────────────────────────────────

def _read_features(pyproject_path: Path) -> list[dict]:
    """Return list of {name, version, ns_github, ns_fs} from the product's pyproject."""
    if not pyproject_path.exists():
        return []
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    refs = read_features_from_data(data)
    result = []
    for ref in refs:
        if "/" in ref:
            ns_part, rest = ref.split("/", 1)
            ns_github = ns_part
            ns_fs = ns_part.replace("-", "_")
        else:
            ns_github = "splent-io"
            ns_fs = "splent_io"
            rest = ref
        if "@" in rest:
            name, version = rest.split("@", 1)
        else:
            name, version = rest, None
        result.append({"name": name, "version": version, "ns_github": ns_github, "ns_fs": ns_fs})
    return result


def _update_pyproject(pyproject_path: Path, ns_github: str, name: str, old_ver: str | None, new_ver: str):
    content = pyproject_path.read_text()
    if old_ver:
        content = content.replace(f"{name}@{old_ver}", f"{name}@{new_ver}")
    else:
        # editable entry: append version
        content = content.replace(f"{ns_github}/{name}", f"{ns_github}/{name}@{new_ver}")
    pyproject_path.write_text(content)


# ── Cache / clone helpers ─────────────────────────────────────────────────────

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
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


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
    rel_target = os.path.relpath(str(target), str(features_dir))
    new_link.symlink_to(rel_target)


# ── Command ───────────────────────────────────────────────────────────────────

@click.command(
    "feature:upgrade",
    short_help="Upgrade declared features to the latest version on GitHub.",
)
@click.argument("feature_ref", required=False)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def feature_upgrade(feature_ref, yes):
    """
    Upgrade one or all features declared in the active product to the latest
    version available on GitHub. Clones the new version if not already cached,
    then updates pyproject.toml and the feature symlink.

    \b
    With no arguments, checks all declared features.
    With <feature_name> (e.g. splent_feature_auth), upgrades only that one.
    """
    token = os.getenv("GITHUB_TOKEN")
    workspace = context.workspace()
    product = context.require_app()

    product_path = workspace / product
    pyproject_path = product_path / "pyproject.toml"
    cache_root = workspace / ".splent_cache" / "features"

    features = _read_features(pyproject_path)
    if not features:
        click.secho("ℹ️  No features declared in pyproject.toml.", fg="yellow")
        return

    if feature_ref:
        name = feature_ref.split("/", 1)[1] if "/" in feature_ref else feature_ref
        features = [f for f in features if f["name"] == name]
        if not features:
            click.secho(f"⚠️  '{name}' not declared in this product.", fg="yellow")
            return

    # ── Resolve latest remote version for each feature ────────────────────────
    click.echo()
    click.secho("  Checking latest versions on GitHub...", fg="bright_black")

    upgrades = []
    for feat in features:
        latest = _latest_remote_version(feat["ns_github"], feat["name"], token)
        if not latest:
            continue

        current = feat["version"]
        if current is None:
            # editable → offer to pin to latest
            upgrades.append({**feat, "latest": latest})
            continue

        try:
            is_newer = Version(latest.lstrip("v")) > Version(current.lstrip("v"))
        except InvalidVersion:
            is_newer = latest != current

        if is_newer:
            upgrades.append({**feat, "latest": latest})

    if not upgrades:
        click.echo()
        click.secho("  ✅ All features are already at the latest version.", fg="green")
        click.echo()
        return

    click.echo()
    click.secho(f"  Features to upgrade ({len(upgrades)}):\n", fg="cyan")
    for u in upgrades:
        cur_label = click.style(u["version"] or "editable", fg="red")
        new_label = click.style(u["latest"], fg="green")
        click.echo(f"    {u['ns_fs']}/{u['name']}    {cur_label} → {new_label}")

    click.echo()
    if not yes and not click.confirm("  Proceed with upgrade?"):
        click.echo("  ❎ Cancelled.")
        raise SystemExit(0)

    click.echo()
    from splent_cli.utils.lifecycle import require_state, resolve_feature_key_from_entry
    from splent_cli.utils.manifest import feature_key

    for u in upgrades:
        # Guard: cannot upgrade a feature with applied migrations
        key = feature_key(u["ns_fs"], u["name"], u["version"])
        try:
            require_state(str(product_path), key, command="feature:upgrade")
        except SystemExit:
            continue

        try:
            _clone_if_missing(u["ns_fs"], u["name"], u["latest"], cache_root)
            _update_pyproject(pyproject_path, u["ns_github"], u["name"], u["version"], u["latest"])
            _update_symlink(product_path, u["ns_fs"], u["name"], u["version"], u["latest"], cache_root)
            click.secho(f"  ✔  {u['ns_fs']}/{u['name']} → {u['latest']}", fg="green")
        except Exception as e:
            click.secho(f"  ✖  {u['ns_fs']}/{u['name']}: {e}", fg="red")

    click.echo()
    click.secho("  Done. Run 'splent product:sync' to reinstall pip dependencies.", fg="cyan")
    click.echo()

    if not token:
        click.secho("  💡 Set GITHUB_TOKEN to avoid rate limits.", fg="yellow")
        click.echo()


cli_command = feature_upgrade
