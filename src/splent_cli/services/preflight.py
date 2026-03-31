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

    if interactive:
        click.echo(click.style("━━ Pre-flight checks", fg="bright_black", bold=True))
        click.echo()

    failed = False

    # [1/2] product:validate
    if interactive:
        click.echo(click.style("  [1/2] product:validate", fg="bright_black"))
    uvl_ok, uvl_msg = run_uvl_check(workspace)
    if uvl_ok:
        if interactive:
            click.secho("        ✅ UVL configuration is satisfiable.", fg="green")
    else:
        if interactive:
            click.secho(f"        🚨 {uvl_msg}", fg="red")
            click.secho("        → Run: splent product:validate", fg="yellow")
        failed = True
    if interactive:
        click.echo()

    # [2/2] feature:diff --all
    if interactive:
        click.echo(click.style("  [2/2] feature:diff --all", fg="bright_black"))
    findings = run_all_product_check(workspace, product_dir)
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    if not errors:
        if interactive:
            if warnings:
                click.secho(
                    f"        ✅ No conflicts. {len(warnings)} warning(s) — "
                    "run 'splent feature:diff --all' to review.",
                    fg="green",
                )
            else:
                click.secho("        ✅ No conflicts detected.", fg="green")
    else:
        if interactive:
            for err in errors:
                click.secho(f"        🚨 [{err['field']}] {err['message']}", fg="red")
            click.secho("        → Run: splent feature:diff --all", fg="yellow")
        failed = True
    if interactive:
        click.echo()

    if failed and interactive:
        click.secho(
            "❌ Pre-flight checks failed. Fix the issues above before proceeding.",
            fg="red",
            bold=True,
        )
        click.echo()

    return not failed
