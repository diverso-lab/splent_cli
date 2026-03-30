import os
import click
from splent_cli.services import context


@click.command(
    "product:deselect",
    short_help="Deselect the active product (enter detached mode).",
)
@click.option(
    "--shell", is_flag=True, help="Output shell commands for eval."
)
@context.requires_product
def product_deselect(shell):
    """Clear SPLENT_APP from .env, entering detached mode."""
    workspace = context.workspace()
    workspace_env_path = str(workspace / ".env")

    # Remove SPLENT_APP from .env file
    if os.path.exists(workspace_env_path):
        with open(workspace_env_path, "r") as f:
            lines = f.readlines()
        new_lines = [line for line in lines if not line.startswith("SPLENT_APP=")]
        with open(workspace_env_path, "w") as f:
            f.writelines(new_lines)

    if shell:
        # For: eval $(splent product:deselect --shell)
        print("unset SPLENT_APP")
    else:
        click.echo("✅ Product deselected.")
        click.echo("   Run: unset SPLENT_APP")


cli_command = product_deselect
