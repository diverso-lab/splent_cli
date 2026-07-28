"""
check:pyproject — Validate the active product's pyproject.toml.
"""

import os
import re

import click
import tomllib

from splent_cli.services import context


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


@click.command(
    "check:pyproject", short_help="Validate pyproject.toml and dependencies."
)
def check_pyproject():
    """Parse pyproject.toml, check dependencies, and validate feature declarations."""
    workspace = str(context.workspace())
    product = context.require_app()
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")

    ok = fail = warn = 0

    def _ok(msg):
        nonlocal ok
        ok += 1
        click.echo(click.style("  [✔] ", fg="green") + msg)

    def _fail(msg):
        nonlocal fail
        fail += 1
        click.echo(click.style("  [✖] ", fg="red") + msg)

    def _warn(msg):
        nonlocal warn
        warn += 1
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
    splent = data.get("tool", {}).get("splent", {})
    base_feats = splent.get("features", [])
    dev_feats = splent.get("features_dev", [])
    prod_feats = splent.get("features_prod", [])

    _ok(f"{len(base_feats)} features in [tool.splent].features")
    if dev_feats:
        _ok(f"{len(dev_feats)} dev-only features")
    if prod_feats:
        _ok(f"{len(prod_feats)} prod-only features")

    # Check for duplicates across feature lists
    def _bare_name(entry):
        name = entry.split("/")[-1] if "/" in entry else entry
        return name.split("@")[0]

    all_entries = base_feats + dev_feats + prod_feats
    seen_names = {}
    for entry in all_entries:
        bare = _bare_name(entry)
        seen_names.setdefault(bare, []).append(entry)

    for bare, entries in seen_names.items():
        if len(entries) > 1:
            short = bare.replace("splent_feature_", "")
            _fail(f"Duplicate feature '{short}': {', '.join(entries)}")

    # Check for inconsistent namespaces
    namespaces = set()
    for entry in all_entries:
        if "/" in entry:
            ns = entry.split("/")[0]
            namespaces.add(ns)
    if len(namespaces) > 1:
        _warn(
            f"Inconsistent namespaces: {', '.join(sorted(namespaces))} — use one format consistently"
        )

    # SPL / UVL
    spl_name = splent.get("spl")
    if spl_name:
        from splent_cli.services import spl_store

        pin = spl_store.read_pin(workspace, spl_name, product=product)
        uvl_path = spl_store.find_uvl(
            workspace, spl_name, version=pin.version, doi=pin.doi
        )
        if uvl_path:
            _ok(f"SPL model {spl_name} (UVL found at {uvl_path})")
        elif pin.fetchable:
            _warn(
                f"SPL model {spl_name} not downloaded yet, run: "
                f"splent spl:fetch {spl_name}"
            )
        else:
            _fail(
                f"SPL model {spl_name} has no UVL on disk and no DOI recorded. "
                f"Add [tool.splent.spl_model] with the DOI, or run: "
                f"splent spl:create {spl_name}"
            )
    else:
        # Legacy fallback
        uvl_cfg = splent.get("uvl", {})
        uvl_file = uvl_cfg.get("file")
        if uvl_file:
            app_path = os.path.join(workspace, product)
            uvl_path = os.path.join(app_path, "uvl", uvl_file)
            if os.path.exists(uvl_path):
                _ok(f"UVL file found: uvl/{uvl_file} (legacy)")
            else:
                _fail(f"UVL file not found: uvl/{uvl_file}")
        else:
            _warn("No SPL configured — set [tool.splent].spl in pyproject.toml")

    click.echo()
    if fail:
        click.secho(f"  {fail} check(s) failed.", fg="red")
        raise SystemExit(1)


cli_command = check_pyproject
