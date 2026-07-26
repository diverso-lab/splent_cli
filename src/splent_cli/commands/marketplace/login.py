"""
splent login — Authenticate against the SPLENT marketplace.

Docker-login ergonomics: prompt for e-mail and password, exchange them for an
API token, and store that token per registry. There is deliberately NO --token
option, because a secret passed in argv lands in the shell history and in the
process table of every user on the machine. CI pipes the token in instead::

    echo "$SPLENT_MARKETPLACE_TOKEN" | splent login --token-stdin

The token is validated with whoami BEFORE anything is written, so a dead
credential is never persisted.
"""

import os
import sys

import click

from splent_cli.commands.marketplace.marketplace_auth_utils import (
    default_token_name,
    field,
    format_expiry,
    format_scopes,
    handle_error,
    resolve_target,
)
from splent_cli.services import credentials, marketplace_api


def _read_token_from_stdin() -> str:
    """Read a pasted token from stdin, refusing anything that is not one line."""
    data = sys.stdin.read() if not sys.stdin.isatty() else ""
    token = data.strip()
    if not token:
        click.secho("  No token arrived on stdin.", fg="red")
        click.echo(
            '  Pipe it in, for example  echo "$SPLENT_MARKETPLACE_TOKEN" '
            "| splent login --token-stdin"
        )
        raise SystemExit(1)
    if any(c.isspace() for c in token):
        click.secho("  The token read from stdin is not a single value.", fg="red")
        click.echo("  Send only the token, with no extra output around it.")
        raise SystemExit(1)
    return token


@click.command(
    "login",
    short_help="Log in to the SPLENT marketplace and store the token.",
)
@click.option(
    "--registry",
    "registry_option",
    default=None,
    metavar="URL",
    help="Marketplace to log in to. Defaults to SPLENT_MARKETPLACE_URL, "
    "then https://marketplace.splent.io.",
)
@click.option(
    "--token-stdin",
    "token_stdin",
    is_flag=True,
    help="Read an existing token from stdin instead of prompting (for CI).",
)
@click.option(
    "--name",
    "token_name",
    default=None,
    metavar="LABEL",
    help="Label for the new token on the server. Default splent-cli@<hostname>.",
)
def login(registry_option, token_stdin, token_name):
    """
    Authenticate against the SPLENT marketplace and store the token locally.

    \b
    The token is stored per registry, so a production marketplace and a local
    one coexist, in a 0600 file that survives a container rebuild
    (SPLENT_CREDENTIALS overrides its location).

    \b
    Examples:
      splent login
      splent login --registry http://localhost:5818
      echo "$TOKEN" | splent login --token-stdin
    """
    target = resolve_target(registry_option)
    click.echo()
    click.echo(click.style(f"  Logging in to {target.url}", bold=True))
    click.echo()

    if token_stdin:
        if token_name:
            click.secho(
                "  The --name label is ignored with --token-stdin, the token "
                "already has one.",
                fg="yellow",
            )
        token = _read_token_from_stdin()
    else:
        label = token_name or default_token_name()
        email = click.prompt("  E-mail", type=str).strip()
        password = click.prompt("  Password", type=str, hide_input=True)
        client = marketplace_api.MarketplaceClient(target.url)
        try:
            created = client.login(
                email, password, label, marketplace_api.DEFAULT_TTL_DAYS
            )
        except marketplace_api.MarketplaceError as e:
            click.echo()
            handle_error(e, url=target.url)
            raise SystemExit(1)
        token = created["token"]

    # Always validate before writing: a token that cannot answer whoami is
    # worthless, and storing it would only produce confusing failures later.
    verifier = marketplace_api.MarketplaceClient(target.url, token=token)
    try:
        identity = verifier.whoami()
    except marketplace_api.MarketplaceError as e:
        click.echo()
        handle_error(e, url=target.url)
        click.secho("  Nothing was stored.", fg="yellow")
        raise SystemExit(1)

    try:
        path = credentials.save(
            target.url,
            token=token,
            identity=identity.get("identity"),
            token_name=identity.get("token_name"),
            scopes=identity.get("scopes"),
            expires_at=identity.get("expires_at"),
        )
    except credentials.CredentialsError as e:
        click.echo()
        click.secho(f"  {e}", fg="red")
        raise SystemExit(1)

    click.echo()
    click.secho(f"  Logged in to {target.url}", fg="green", bold=True)
    field("Identity", identity.get("identity") or "(not reported)")
    field("Scopes", format_scopes(identity.get("scopes")))
    field("Token", identity.get("token_name") or "(unnamed)")
    field("Expires", format_expiry(identity.get("expires_at")))
    field("Stored in", f"{path} (mode 0600)")

    if (os.getenv(credentials.TOKEN_ENV) or "").strip():
        click.echo()
        click.secho(
            f"  Note that {credentials.TOKEN_ENV} is set and takes precedence "
            "over the stored token.",
            fg="yellow",
        )
        click.secho(
            "  Unset it to use the token that was just stored.",
            fg="yellow",
        )
    click.echo()


cli_command = login
