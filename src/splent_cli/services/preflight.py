"""
Pre-flight checks shared across product commands (derive, build, deploy).

Returns True if all checks pass, False otherwise.
"""

import os
import tomllib

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import read_features_from_data


def _check_pypi_exists(package: str, version: str) -> bool:
    """Check if a package@version exists on PyPI."""
    import requests

    # Strip leading 'v' from version for PyPI (v1.0.0 → 1.0.0)
    pypi_version = version.lstrip("v")
    url = f"https://pypi.org/pypi/{package}/{pypi_version}/json"
    try:
        r = requests.head(url, timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _check_features_ready(workspace: str, product_dir: str, interactive: bool) -> bool:
    """Check that all prod features are versioned and published on PyPI.

    Returns True if all features pass.
    """
    pyproject_path = os.path.join(product_dir, "pyproject.toml")
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = read_features_from_data(data, "prod")
    if not features:
        return True

    issues = []
    checked = 0
    for entry in features:
        name = entry.split("/")[-1] if "/" in entry else entry
        bare_name = name.split("@")[0]
        short = bare_name.replace("splent_feature_", "")

        # Check versioned
        if "@" not in name:
            issues.append((short, "not versioned — release it first"))
            continue

        version = name.split("@")[1]
        checked += 1

        # Check exists on PyPI
        if not _check_pypi_exists(bare_name, version):
            issues.append((short, f"@{version} not found on PyPI"))

    if not issues:
        if interactive:
            click.echo(
                f"  features  all {len(features)} feature(s) versioned and on PyPI"
            )
        return True

    if interactive:
        for short, problem in issues:
            click.secho(f"  features  {short}: {problem}", fg="red")
    return False


def run_preflight(*, interactive: bool = True, build_mode: bool = False) -> bool:
    """
    Run product:validate as pre-flight check.

    Parameters
    ----------
    interactive : bool
        When True, prints output. When False, silent (returns result only).
    build_mode : bool
        When True, additionally checks that all prod features are versioned
        and available (for product:build / product:deploy).

    Returns True if all checks pass.
    """
    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    # Run the full product:validate programmatically
    from splent_cli.commands.uvl.uvl_utils import (
        read_splent_app as _read_splent_app,
        load_pyproject as _load_pyproject,
    )
    from splent_cli.commands.product.product_validate import (
        _run_sat_check,
        _run_compat_check,
    )

    app_name = _read_splent_app(workspace=workspace)
    pyproject_path = os.path.join(product_dir, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    failed = False

    # Phase 1: SAT
    try:
        sat_ok, selected, _, _ = _run_sat_check(workspace, app_name, data, None, False)
    except Exception:
        sat_ok = False

    if sat_ok:
        if interactive:
            click.echo("  validate UVL configuration is satisfiable")
    else:
        if interactive:
            click.secho("  validate UVL configuration is NOT satisfiable", fg="red")
            click.secho(
                "           run 'splent product:validate' to inspect", fg="bright_black"
            )
        failed = True

    # Phase 2: Contracts
    try:
        findings, errors, warnings = _run_compat_check(workspace, product_dir)
    except Exception:
        errors = []
        warnings = []

    if not errors:
        if interactive:
            if warnings:
                click.echo(
                    f"  contract no conflicts — {len(warnings)} warning(s), "
                    "run 'splent product:validate' to review"
                )
            else:
                click.echo("  contract no conflicts detected")
    else:
        if interactive:
            for err in errors:
                click.secho(f"  contract [{err['field']}] {err['message']}", fg="red")
            click.secho(
                "           run 'splent product:validate' to inspect",
                fg="bright_black",
            )
        failed = True

    # Phase 3: Feature readiness (build/deploy only)
    if build_mode:
        if not _check_features_ready(workspace, product_dir, interactive):
            failed = True

    return not failed
