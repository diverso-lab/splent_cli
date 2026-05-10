import click

from splent_cli.services import marketplace


@click.command(
    "marketplace:logout",
    short_help="Remove Marketplace credentials from the workspace .env.",
)
@click.option("--shell", is_flag=True, help="Output shell commands for eval.")
def marketplace_logout(shell):
    marketplace.set_env_var(marketplace.MARKETPLACE_AUTH_VAR, "false")

    if shell:
        print(f"export {marketplace.MARKETPLACE_AUTH_VAR}=false")
    else:
        click.secho("  Marketplace logout done.", fg="green")


cli_command = marketplace_logout
