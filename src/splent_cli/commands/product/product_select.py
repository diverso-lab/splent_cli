import os
import sys
import click


@click.command(
    "product:select",
    short_help="Select the active app (updates .env and session env vars)",
)
@click.argument("app_name", required=True)
@click.option(
    "--shell", is_flag=True, help="Output shell commands instead of applying directly"
)
def select_app(app_name, shell):
    workspace_env_path = "/workspace/.env"
    product_path = os.path.join("/workspace", app_name)

    # Check product exists
    if not os.path.isdir(product_path):
        click.echo(f"Error: product '{app_name}' not found in /workspace", err=True)
        sys.exit(1)

    # Ensure .env exists
    if not os.path.exists(workspace_env_path):
        open(workspace_env_path, "w").close()

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

    with open(workspace_env_path, "w") as f:
        f.writelines(new_lines)

    # If --shell, print commands for eval
    if shell:
        print(f"export SPLENT_APP={app_name}")
        print("source /workspace/.env")
        print("type set_prompt >/dev/null 2>&1 && set_prompt || true")
