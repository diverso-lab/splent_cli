"""
check:pyproject — Validate the active product's pyproject.toml.
"""
import os
import re

import click
import tomllib

from splent_cli.services import context
from splent_cli.utils.feature_utils import read_features_from_data


def _find_missing_pkgs(deps: list) -> list:
    import importlib.metadata
    missing = []
    for dep in deps:
        pkg = re.split(r"[=<>!~\[]", dep)[0].strip()
        if pkg:
            try:
                importlib.metadata.version(pkg)
            except Exception:
                missing.append(pkg)
    return missing


@click.command("check:pyproject", short_help="Validate pyproject.toml and dependencies.")
def check_pyproject():
    """Parse pyproject.toml, check dependencies, and validate feature declarations."""
    workspace = str(context.workspace())
    product = context.require_app()
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")

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

    # Parse
    if not os.path.exists(pyproject_path):
        _fail("pyproject.toml not found")
        raise SystemExit(1)

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        _ok("pyproject.toml parsed successfully")
    except Exception as e:
        _fail(f"Invalid pyproject.toml: {e}")
        raise SystemExit(1)

    # Project metadata
    name = data.get("project", {}).get("name")
    version = data.get("project", {}).get("version")
    if name:
        _ok(f"Project name: {name}")
    else:
        _warn("No project name declared")
    if version:
        _ok(f"Project version: {version}")
    else:
        _warn("No project version declared")

    # Dependencies
    deps = data.get("project", {}).get("dependencies", [])
    missing = _find_missing_pkgs(deps)
    if missing:
        _fail(f"Missing dependencies: {', '.join(missing)}")
    else:
        _ok(f"All {len(deps)} dependencies satisfied")

    # Features
    features = read_features_from_data(data)
    _ok(f"{len(features)} features declared in [tool.splent.features]")

    splent = data.get("tool", {}).get("splent", {})
    dev_feats = splent.get("features_dev", [])
    prod_feats = splent.get("features_prod", [])
    if dev_feats:
        _ok(f"{len(dev_feats)} dev-only features")
    if prod_feats:
        _ok(f"{len(prod_feats)} prod-only features")

    # UVL
    uvl_cfg = splent.get("uvl", {})
    uvl_file = uvl_cfg.get("file")
    if uvl_file:
        app_path = os.path.join(workspace, product)
        uvl_path = os.path.join(app_path, "uvl", uvl_file)
        if os.path.exists(uvl_path):
            _ok(f"UVL file found: uvl/{uvl_file}")
        else:
            _fail(f"UVL file not found: uvl/{uvl_file}")
    else:
        _warn("No UVL file configured")

    click.echo()
    if fail:
        click.secho(f"  {fail} check(s) failed.", fg="red")
        raise SystemExit(1)


cli_command = check_pyproject
