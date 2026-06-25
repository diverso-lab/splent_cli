import os
import click
from splent_cli.services import context
from splent_cli.utils.io_utils import atomic_write


@click.command(
    "product:select",
    short_help="Select the active product (sets SPLENT_APP in .env).",
)
@click.argument("app_name", required=True)
@click.option(
    "--shell", is_flag=True, help="Output shell commands instead of applying directly"
)
def select_app(app_name, shell):
    workspace = context.workspace()
    workspace_env_path = str(workspace / ".env")
    product_path = str(workspace / app_name)

    # Check product exists
    if not os.path.isdir(product_path):
        click.echo(f"Error: product '{app_name}' not found in {workspace}", err=True)
        raise SystemExit(1)

    # Ensure .env exists
    if not os.path.exists(workspace_env_path):
        from pathlib import Path

        Path(workspace_env_path).touch()

    # Update .env
    lines = []
    with open(workspace_env_path, "r") as f:
        lines = f.readlines()

    found = False
    new_lines = []
    for line in lines:
        if line.startswith("SPLENT_APP="):
            new_lines.append(f"SPLENT_APP={app_name}\n")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"SPLENT_APP={app_name}\n")

    try:
        atomic_write(workspace_env_path, "".join(new_lines))
    except OSError as e:
        click.echo(
            f"Error: failed to update SPLENT_APP in {workspace_env_path}: {e}",
            err=True,
        )
        raise SystemExit(1)

    # If --shell, print commands for eval
    if shell:
        print(f"export SPLENT_APP={app_name}")
