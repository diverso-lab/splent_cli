"""
Pre-flight checks shared across product commands (derive, build, deploy).

Returns True if all checks pass, False otherwise.
"""

import os

import click

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import run_uvl_check
from splent_cli.commands.feature.feature_diff import run_all_product_check


def run_preflight(*, interactive: bool = True) -> bool:
    """
    Run UVL and feature:diff pre-flight checks.

    Parameters
    ----------
    interactive : bool
        When True, prints output. When False, silent (returns result only).

    Returns True if all checks pass.
    """
    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    failed = False

    # validate — UVL satisfiability
    uvl_ok, uvl_msg = run_uvl_check(workspace)
    if uvl_ok:
        if interactive:
            click.echo("  validate UVL configuration is satisfiable")
    else:
        if interactive:
            click.secho(f"  validate {uvl_msg}", fg="red")
            click.secho(
                "           run 'splent product:validate' to inspect", fg="bright_black"
            )
        failed = True

    # contract — feature:diff --all
    findings = run_all_product_check(workspace, product_dir)
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    if not errors:
        if interactive:
            if warnings:
                click.echo(
                    f"  contract no conflicts — {len(warnings)} warning(s), "
                    "run 'splent feature:diff --all' to review"
                )
            else:
                click.echo("  contract no conflicts detected")
    else:
        if interactive:
            for err in errors:
                click.secho(f"  contract [{err['field']}] {err['message']}", fg="red")
            click.secho(
                "           run 'splent feature:diff --all' to inspect",
                fg="bright_black",
            )
        failed = True

    return not failed
