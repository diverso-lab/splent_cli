"""
splent logout — Drop the stored marketplace credential.

Revoking on the server is best effort (a marketplace that is down must not
leave a token wedged on the laptop), deleting locally is guaranteed. Having
nothing stored is not an error, so scripts can call logout unconditionally.
"""

import os

import click

from splent_cli.commands.marketplace.marketplace_auth_utils import (
    report_error,
    resolve_target,
)
from splent_cli.services import credentials, marketplace_api


@click.command(
    "logout",
    short_help="Remove the stored SPLENT marketplace token and revoke it.",
)
@click.option(
    "--registry",
    "registry_option",
    default=None,
    metavar="URL",
    help="Marketplace to log out of. Defaults to SPLENT_MARKETPLACE_URL, "
    "then https://marketplace.splent.io.",
)
def logout(registry_option):
    """
    Log out of the SPLENT marketplace.

    \b
    The stored token is revoked on the server when it can be reached, and is
    always removed locally. A token coming from SPLENT_MARKETPLACE_TOKEN is
    not managed by the CLI, so it is reported instead of revoked.
    """
    target = resolve_target(registry_option)
    click.echo()

    try:
        entry = credentials.get(target.url)
    except credentials.CredentialsError as e:
        click.secho(f"  {e}", fg="yellow")
        entry = None

    env_token = (os.getenv(credentials.TOKEN_ENV) or "").strip()

    if not entry:
        click.secho(f"  Nothing stored for {target.url}", fg="yellow")
        if env_token:
            click.echo(
                f"  A token is coming from {credentials.TOKEN_ENV}. "
                f"Unset it to finish logging out."
            )
        click.echo()
        return

    token = (entry.get("token") or "").strip()
    if token:
        client = marketplace_api.MarketplaceClient(target.url, token=token)
        try:
            client.revoke_current()
            click.secho("  Token revoked on the server.", fg="green")
        except marketplace_api.MarketplaceError as e:
            # Best effort on purpose. A token that is already dead, or a
            # marketplace that is down, must not block the local delete.
            click.secho(
                "  Could not revoke the token on the server, removing it "
                "locally anyway.",
                fg="yellow",
            )
            report_error(e, url=target.url)
            if not e.dead_token:
                click.secho(
                    f"  Revoke it from the marketplace at {target.url} if it "
                    "is still listed there.",
                    fg="yellow",
                )

    try:
        credentials.delete(target.url)
    except credentials.CredentialsError as e:
        click.secho(f"  {e}", fg="red")
        raise SystemExit(1)

    click.secho(f"  Logged out of {target.url}", fg="green", bold=True)
    click.echo(f"  Removed the stored token from {credentials.store_path()}")
    if env_token:
        click.secho(
            f"  A token is still coming from {credentials.TOKEN_ENV}. "
            f"Unset it to finish logging out.",
            fg="yellow",
        )
    click.echo()


cli_command = logout
