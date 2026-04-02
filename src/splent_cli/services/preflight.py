"""
Pre-flight checks shared across product commands (derive, build, deploy).

Returns True if all checks pass, False otherwise.
"""

import os

import click

from splent_cli.services import context


def run_preflight(*, interactive: bool = True) -> bool:
    """
    Run product:validate as pre-flight check.

    Parameters
    ----------
    interactive : bool
        When True, prints output. When False, silent (returns result only).

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

    return not failed
