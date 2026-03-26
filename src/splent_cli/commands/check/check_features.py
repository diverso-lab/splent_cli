"""
check:features — Validate feature cache, symlinks, pip install, and git state.
"""
import os
import subprocess
import importlib.metadata

import click
import tomllib

from splent_cli.services import context
from splent_cli.utils.feature_utils import read_features_from_data


def _pkg_installed(name: str) -> bool:
    try:
        importlib.metadata.version(name)
        return True
    except Exception:
        return False


@click.command("check:features", short_help="Validate feature cache, symlinks, and install state.")
def check_features():
    """Check every declared feature: cache entry, symlink, pip install, git state."""
    workspace = str(context.workspace())
    product = context.require_app()
    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    ok = fail = warn = 0

    def _ok(msg):
        nonlocal ok; ok += 1
        click.echo(click.style("  [✔] ", fg="green") + msg)

    def _fail(msg):
        nonlocal fail; fail += 1
        click.echo(click.style("  [✖] ", fg="red") + msg)

    def _warn(msg):
        nonlocal warn; warn += 1
        click.echo(click.style("  [⚠] ", fg="yellow") + msg)

    click.echo()

    if not os.path.exists(pyproject_path):
        _fail("pyproject.toml not found")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    env = os.getenv("SPLENT_ENV")
    features = read_features_from_data(data, env)

    if not features:
        _warn("No features declared")
        click.echo()
        return

    cache_root = os.path.join(workspace, ".splent_cache", "features")
    features_dir = os.path.join(product_path, "features")

    for entry in features:
        # Parse entry
        if "/" in entry:
            org_raw, rest = entry.split("/", 1)
            org_safe = org_raw.replace("-", "_").replace(".", "_")
        else:
            org_safe = "splent_io"
            rest = entry

        name, _, version = rest.partition("@")
        label = f"{org_safe}/{name}@{version}" if version else f"{org_safe}/{name}"
        dir_name = f"{name}@{version}" if version else name

        click.echo(click.style(f"  {label}", bold=True))

        # 1. Cache
        cache_dir = os.path.join(cache_root, org_safe, dir_name)
        if os.path.isdir(cache_dir):
            src_dir = os.path.join(cache_dir, "src", org_safe, name)
            if os.path.isdir(src_dir):
                _ok("Cache OK")
            else:
                _fail("Cache exists but missing src/ structure")
        else:
            _fail(f"Not in cache: {cache_dir}")

        # 2. Symlink
        link_path = os.path.join(features_dir, org_safe, dir_name)
        if os.path.islink(link_path):
            if os.path.exists(link_path):
                target = os.readlink(link_path)
                if os.path.isabs(target):
                    _warn(f"Symlink uses absolute path (should be relative)")
                else:
                    _ok("Symlink OK (relative)")
            else:
                _fail("Broken symlink")
        elif os.path.exists(link_path):
            _warn("Expected symlink but found directory/file")
        else:
            _fail("No symlink in product/features/")

        # 3. Pip install
        if _pkg_installed(name):
            _ok("pip installed")
        else:
            _warn("Not pip-installed (run product:run to install)")

        # 4. Git state (editable only)
        if not version:
            try:
                r = subprocess.run(
                    ["git", "-C", cache_dir, "status", "--porcelain"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode != 0:
                    _warn("Not a git repo")
                elif r.stdout.strip():
                    _warn("Uncommitted changes")
                else:
                    _ok("Git clean")
            except (subprocess.TimeoutExpired, FileNotFoundError):
                _warn("Could not check git status")

        click.echo()

    if fail:
        click.secho(f"  {fail} check(s) failed.", fg="red")
        raise SystemExit(1)
    else:
        click.secho(f"  All features OK ({ok} checks passed).", fg="green")
    click.echo()


cli_command = check_features
