import os

import click

from splent_cli.services import marketplace


@click.command(
    "marketplace:login",
    short_help="Store Marketplace credentials in the workspace .env.",
)
@click.option(
    "--token",
    default=None,
    help="Marketplace/API access token. Defaults to SPLENT_API_TOKEN.",
)
@click.option(
    "--url",
    default=None,
    help="Optional Marketplace API URL. Defaults to SPLENT_API_URL.",
)
@click.option("--shell", is_flag=True, help="Output shell commands for eval.")
def marketplace_login(token, url, shell):
    token = (
        token if token is not None else os.getenv(marketplace.MARKETPLACE_TOKEN_VAR)
    )
    token = token.strip() if token else ""
    api_url = (
        url
        or os.getenv(marketplace.MARKETPLACE_API_URL_VAR)
        or marketplace.DEFAULT_API_URL
    ).rstrip("/")

    try:
        valid_token = marketplace.validate_api_token(api_url, token or None)
    except marketplace.MarketplaceLoginError as exc:
        click.secho(f"❌ {exc}", fg="red")
        raise SystemExit(1)

    if not valid_token:
        click.secho("❌ Invalid Marketplace/API token.", fg="red")
        raise SystemExit(1)

    marketplace.set_env_var(marketplace.MARKETPLACE_API_URL_VAR, api_url)
    if token:
        marketplace.set_env_var(marketplace.MARKETPLACE_TOKEN_VAR, token)
    else:
        marketplace.unset_env_var(marketplace.MARKETPLACE_TOKEN_VAR)
    marketplace.set_env_var(marketplace.MARKETPLACE_AUTH_VAR, "true")

    if shell:
        print(f"export {marketplace.MARKETPLACE_API_URL_VAR}={api_url}")
        if token:
            print(f"export {marketplace.MARKETPLACE_TOKEN_VAR}={token}")
        else:
            print(f"unset {marketplace.MARKETPLACE_TOKEN_VAR}")
        print(f"export {marketplace.MARKETPLACE_AUTH_VAR}=true")
    else:
        click.secho("  Marketplace login saved.", fg="green")
        click.echo("  Run: splent feature:search <query>")


cli_command = marketplace_login
