import os

import click

from splent_cli.services import context


@click.command(
    "product:signals",
    short_help="Show all feature signals and their listeners.",
)
@context.requires_product
def product_signals():
    """Show all signals defined by features in the active product.

    For each signal, displays which feature emits it and which features listen.
    """
    product = context.require_app()

    os.environ.setdefault("SPLENT_ENV", "dev")
    from splent_cli.utils.dynamic_imports import get_app

    get_app()

    from splent_framework.signals.registry import get_registry

    registry = get_registry()

    if not registry:
        click.echo()
        click.secho(f"  {product}: no signals registered.", fg="yellow")
        click.echo()
        return

    click.echo()
    click.secho(f"  {product}  —  {len(registry)} signal(s)", bold=True)
    click.echo()

    col_signal = max(len(name) for name in registry)
    col_signal = max(col_signal, 6)
    col_provider = max(len(info["provider"]) for info in registry.values())
    col_provider = max(col_provider, 8)

    header = f"  {'SIGNAL':<{col_signal}}  {'EMITTED BY':<{col_provider}}  LISTENERS"
    click.secho(header, fg="cyan")
    click.echo("  " + "\u2500" * (col_signal + col_provider + 30))

    for name, info in sorted(registry.items()):
        provider = info["provider"]
        listeners = info["listeners"]

        provider_styled = click.style(f"{provider:<{col_provider}}", fg="yellow")

        if listeners:
            listeners_str = ", ".join(sorted(listeners))
        else:
            listeners_str = click.style("(none)", fg="bright_black")

        click.echo(f"  {name:<{col_signal}}  {provider_styled}  {listeners_str}")

    click.echo()


cli_command = product_signals
