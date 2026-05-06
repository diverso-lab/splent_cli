import click

from splent_cli.services import marketplace


@click.command(
    "marketplace:logout",
    short_help="Remove Marketplace credentials from the workspace .env.",
)
@click.option("--shell", is_flag=True, help="Output shell commands for eval.")
def marketplace_logout(shell):
    marketplace.unset_env_var(marketplace.MARKETPLACE_TOKEN_VAR)
    marketplace.unset_env_var(marketplace.MARKETPLACE_API_URL_VAR)

    if shell:
        print(f"unset {marketplace.MARKETPLACE_TOKEN_VAR}")
        print(f"unset {marketplace.MARKETPLACE_API_URL_VAR}")
    else:
        click.secho("  Marketplace logout done.", fg="green")


cli_command = marketplace_logout
