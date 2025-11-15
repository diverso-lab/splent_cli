import os
import sys
import click


@click.command(
    "product:select",
    short_help="Select the active app (updates .env, prompt, and session env vars)",
)
@click.argument("app_name", required=True)
@click.option(
    "--shell", is_flag=True, help="Output shell commands instead of applying directly"
)
def select_app(app_name, shell):
    workspace_env_path = "/workspace/.env"
    splent_env_path = os.path.expanduser("~/.splent_env")
    product_path = os.path.join("/workspace", app_name)

    # ✅ Check that the product exists before doing anything
    if not os.path.isdir(product_path):
        click.echo(f"Error: product '{app_name}' not found in /workspace", err=True)
        sys.exit(1)

    # --- Ensure /workspace/.env exists ---
    if not os.path.exists(workspace_env_path):
        open(workspace_env_path, "w").close()

    # --- Update /workspace/.env ---
    lines = []
    if os.path.exists(workspace_env_path):
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

    # --- Update ~/.splent_env ---
    with open(splent_env_path, "w") as f:
        f.write(f"export SPLENT_APP={app_name}\n")
        f.write("source /workspace/.env\n")

    # --- Ensure ~/.bashrc loads the environment ---
    bashrc = os.path.expanduser("~/.bashrc")
    if os.path.exists(bashrc):
        with open(bashrc, "r") as f:
            bashrc_content = f.read()
        if "source ~/.splent_env" not in bashrc_content:
            with open(bashrc, "a") as f:
                f.write(
                    "\n# Automatically load SPLENT environment\nsource ~/.splent_env\n"
                )

    # --- If --shell is used, print only the export lines (no emojis or colors) ---
    if shell:
        print(f"export SPLENT_APP={app_name}")
        print("source /workspace/.env")
        print("type set_prompt >/dev/null 2>&1 && set_prompt || true")

    # 🔇 No extra output (keeps Docker CLI prompt clean)
