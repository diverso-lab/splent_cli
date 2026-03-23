import os

import click

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_check import run_uvl_check
from splent_cli.commands.feature.feature_diff import run_all_product_check
from splent_cli.commands.product.product_sync import product_sync
from splent_cli.commands.product.product_env import product_env
from splent_cli.commands.product.product_up import product_up
from splent_cli.commands.product.product_run import product_runc
from splent_cli.commands.product.product_port import product_port


@click.command(
    "product:derive",
    short_help="Derive and launch the active product (SPL derivation pipeline).",
)
@click.option("--dev", "mode", flag_value="dev", help="Derive in development mode.")
@click.option("--prod", "mode", flag_value="prod", help="Derive in production mode.")
def product_derive(mode):
    """
    Full SPL product derivation pipeline.

    \b
    Runs two pre-flight checks before the pipeline:
      1. uvl:check  — feature selection must be satisfiable under the UVL model.
      2. feature:diff --all  — no ERROR-level conflicts between feature contracts.

    \b
    --dev runs (after pre-flight):
      1. product:sync
      2. product:env --generate --all --dev
      3. product:env --merge --dev
      4. product:up --dev
      5. product:run --dev
      6. product:port

    --prod is not yet available.
    """
    if not mode:
        click.echo("❌ You must specify --dev or --prod.")
        raise SystemExit(1)

    if mode == "prod":
        click.echo(
            click.style("🚧  --prod derivation is not yet available.", fg="yellow")
        )
        raise SystemExit(0)

    workspace = str(context.workspace())
    product   = context.require_app()
    product_dir = os.path.join(workspace, product)

    click.echo(click.style("\n🧬 SPL Product Derivation — dev\n", fg="cyan", bold=True))

    # ── Pre-flight checks ──────────────────────────────────────────────────
    click.echo(click.style("━━ Pre-flight checks", fg="bright_black", bold=True))
    click.echo()

    preflight_failed = False

    # [pre 1/2] uvl:check
    click.echo(click.style("  [1/2] uvl:check", fg="bright_black"))
    uvl_ok, uvl_msg = run_uvl_check(workspace)
    if uvl_ok:
        click.secho("        ✅ UVL configuration is satisfiable.", fg="green")
    else:
        click.secho(f"        🚨 {uvl_msg}", fg="red")
        click.secho("        → Run: splent uvl:check", fg="yellow")
        preflight_failed = True
    click.echo()

    # [pre 2/2] feature:diff --all
    click.echo(click.style("  [2/2] feature:diff --all", fg="bright_black"))
    findings = run_all_product_check(workspace, product_dir)
    errors   = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    if not errors:
        if warnings:
            click.secho(
                f"        ✅ No conflicts. {len(warnings)} warning(s) — "
                "run 'splent feature:diff --all' to review.",
                fg="green",
            )
        else:
            click.secho("        ✅ No conflicts detected.", fg="green")
    else:
        for err in errors:
            click.secho(
                f"        🚨 [{err['field']}] {err['message']}", fg="red"
            )
        click.secho("        → Run: splent feature:diff --all", fg="yellow")
        preflight_failed = True
    click.echo()

    if preflight_failed:
        click.secho(
            "❌ Pre-flight checks failed. Fix the issues above before deriving the product.",
            fg="red",
            bold=True,
        )
        click.echo()
        raise SystemExit(1)

    click.echo(click.style(f"  {'─' * 70}\n", fg="bright_black"))

    # ── Derivation pipeline ────────────────────────────────────────────────
    ctx = click.get_current_context()

    click.echo(click.style("━━ [1/6] product:sync", fg="bright_black"))
    ctx.invoke(product_sync, force=False)

    click.echo(
        click.style("\n━━ [2/6] product:env --generate --all --dev", fg="bright_black")
    )
    ctx.invoke(
        product_env, generate=True, merge=False, env_name="dev", process_all=True
    )

    click.echo(click.style("\n━━ [3/6] product:env --merge --dev", fg="bright_black"))
    ctx.invoke(
        product_env, generate=False, merge=True, env_name="dev", process_all=False
    )

    click.echo(click.style("\n━━ [4/6] product:up --dev", fg="bright_black"))
    ctx.invoke(product_up, dev=True, prod=False)

    click.echo(click.style("\n━━ [5/6] product:run --dev", fg="bright_black"))
    ctx.invoke(product_runc, env_dev=True, env_prod=False)

    click.echo(click.style("\n━━ [6/6] product:port", fg="bright_black"))
    ctx.invoke(product_port, env_flag="dev")

    click.echo(click.style("\n✅ Product derived successfully.", fg="green", bold=True))
