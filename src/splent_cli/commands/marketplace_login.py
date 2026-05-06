import click

from splent_cli.services import marketplace


@click.command(
    "marketplace:login",
    short_help="Store Marketplace credentials in the workspace .env.",
)
@click.option(
    "--token",
    default=None,
    help="Marketplace access token. If omitted, you will be prompted.",
)
@click.option(
    "--url",
    default=None,
    help="Optional Marketplace API URL.",
)
@click.option("--shell", is_flag=True, help="Output shell commands for eval.")
def marketplace_login(token, url, shell):
    token = token or click.prompt("Marketplace token", hide_input=True)
    token = token.strip()
    if not token:
        raise click.ClickException("Marketplace token is required.")

    api_url = (url or marketplace.DEFAULT_API_URL).rstrip("/")

    try:
        valid_token = marketplace.validate_api_token(api_url, token)
    except marketplace.MarketplaceLoginError as exc:
        click.secho(f"❌ {exc}", fg="red")
        raise SystemExit(1)

    if not valid_token:
        click.secho("❌ Invalid Marketplace token.", fg="red")
        raise SystemExit(1)

    marketplace.set_env_var(marketplace.MARKETPLACE_API_URL_VAR, api_url)
    marketplace.set_env_var(marketplace.MARKETPLACE_TOKEN_VAR, token)

    if shell:
        print(f"export {marketplace.MARKETPLACE_API_URL_VAR}={api_url}")
        print(f"export {marketplace.MARKETPLACE_TOKEN_VAR}={token}")
    else:
        click.secho("  Marketplace login saved.", fg="green")
        click.echo("  Run: splent feature:search <query>")


cli_command = marketplace_login
