"""
splent whoami — Show who this machine is on the SPLENT marketplace.

Prints the registry, the identity behind the token, its scopes, its expiry and
WHERE the token came from (the credential file or an environment variable).
That last line is the one that explains why CI and a laptop behave
differently, so it is never omitted.

When the marketplace cannot be reached the command says exactly that and shows
the cached identity marked as cached, instead of implying you are logged out.
"""

import click

from splent_cli.commands.marketplace.marketplace_auth_utils import (
    credential_source_label,
    detail,
    field,
    format_expiry,
    format_scopes,
    handle_error,
    resolve_target,
    retry_hint,
)
from splent_cli.services import credentials, marketplace_api


def _print_cached(entry: dict) -> None:
    field("Identity", f"{entry.get('identity') or '(unknown)'} (cached)")
    field("Scopes", f"{format_scopes(entry.get('scopes'))} (cached)")
    field("Token", f"{entry.get('token_name') or '(unnamed)'} (cached)")
    field("Expires", f"{format_expiry(entry.get('expires_at'))} (cached)")


@click.command(
    "whoami",
    short_help="Show the SPLENT marketplace identity of the stored token.",
)
@click.option(
    "--registry",
    "registry_option",
    default=None,
    metavar="URL",
    help="Marketplace to ask. Defaults to SPLENT_MARKETPLACE_URL, "
    "then https://marketplace.splent.io.",
)
def whoami(registry_option):
    """
    Show the identity, scopes and expiry of the marketplace token in use.

    \b
    The reported source tells you which credential won: the environment
    variable SPLENT_MARKETPLACE_TOKEN always takes precedence over the
    credential file.
    """
    target = resolve_target(registry_option)
    click.echo()

    try:
        cred = credentials.resolve(target.url)
    except credentials.CredentialsError as e:
        click.secho(f"  {e}", fg="red")
        raise SystemExit(1)

    if cred is None:
        click.secho(f"  Not logged in to {target.url}", fg="yellow")
        field("Registry", f"{target.url} ({target.origin_label})")
        field("Store", str(credentials.store_path()))
        click.echo()
        click.echo("  Run splent login to authenticate.")
        click.echo()
        raise SystemExit(1)

    source = credential_source_label(cred)
    client = marketplace_api.MarketplaceClient(target.url, token=cred.token)
    try:
        identity = client.whoami()
    except marketplace_api.MarketplaceError as e:
        # Unreachable and rate limited are the same situation for this command:
        # the stored credential is untouched, we simply could not ask. Falling
        # through to the generic report would read like a logout.
        if e.unreachable or e.rate_limited:
            if e.rate_limited:
                click.secho(
                    f"  The marketplace at {target.url} is rate limiting this "
                    "client.",
                    fg="yellow",
                )
                click.echo(f"  {retry_hint(e.retry_after)}")
            else:
                click.secho(f"  Marketplace unreachable at {target.url}", fg="yellow")
                detail(e)
            click.echo("  You are still logged in locally, this is not a logout.")
            click.echo()
            field("Registry", f"{target.url} ({target.origin_label})")
            if cred.entry:
                _print_cached(cred.entry)
            else:
                click.echo(
                    "  No cached identity is available for this token, "
                    "so only the source is known."
                )
            field("Source", source)
            click.echo()
            raise SystemExit(1)

        handle_error(e, url=target.url, forget=not cred.from_environment)
        if cred.from_environment and e.dead_token:
            click.secho(
                f"  The token comes from {credentials.TOKEN_ENV}, so unset or "
                "replace it there.",
                fg="yellow",
            )
        click.echo()
        raise SystemExit(1)

    field("Registry", f"{target.url} ({target.origin_label})")
    field("Identity", identity.get("identity") or "(not reported)")
    field("Scopes", format_scopes(identity.get("scopes")))
    field("Token", identity.get("token_name") or "(unnamed)")
    field("Expires", format_expiry(identity.get("expires_at")))
    field("Source", source)
    click.echo()

    # Keep the cache honest so the offline view above stays accurate. Best
    # effort: failing to refresh must never fail a successful whoami.
    if not cred.from_environment:
        try:
            credentials.save(
                target.url,
                token=cred.token,
                identity=identity.get("identity"),
                token_name=identity.get("token_name"),
                scopes=identity.get("scopes"),
                expires_at=identity.get("expires_at"),
            )
        except credentials.CredentialsError:
            pass


cli_command = whoami
