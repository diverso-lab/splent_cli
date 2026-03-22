import click

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
    --dev runs:
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

    ctx = click.get_current_context()

    click.echo(click.style("\n🧬 SPL Product Derivation — dev\n", fg="cyan", bold=True))

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
