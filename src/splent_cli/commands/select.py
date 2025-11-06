import os
import click

@click.command("select", help="Select the active SPLENT app (updates .env, prompt, and session env vars)")
@click.argument("app_name", required=True)
@click.option("--shell", is_flag=True, help="Output shell commands instead of applying directly")
def select_app(app_name, shell):
    if os.getenv("SPLENT_SILENT") == "1":
        click.echo = lambda *a, **k: None

    workspace_env_path = "/workspace/.env"
    splent_env_path = os.path.expanduser("~/.splent_env")
    product_path = os.path.join("/workspace", app_name)

    if not os.path.exists(workspace_env_path):
        click.echo("⚙️  /workspace/.env not found — creating it.")
        open(workspace_env_path, "w").close()

    if not os.path.isdir(product_path):
        click.echo(f"❌ Product folder not found: {product_path}")
        return

    # --- SIEMPRE actualiza /workspace/.env ---
    try:
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
            f.flush()
            os.fsync(f.fileno())

        # --- Actualiza ~/.splent_env también ---
        with open(splent_env_path, "w") as f:
            f.write(f'export SPLENT_APP={app_name}\n')
            f.write('source /workspace/.env\n')

        # --- Asegurar que ~/.bashrc lo cargue ---
        bashrc = os.path.expanduser("~/.bashrc")
        if os.path.exists(bashrc):
            with open(bashrc, "r") as f:
                bashrc_content = f.read()
            if "source ~/.splent_env" not in bashrc_content:
                with open(bashrc, "a") as f:
                    f.write('\n# Load SPLENT environment automatically\nsource ~/.splent_env\n')

        click.echo(click.style(f"✅ Updated /workspace/.env with SPLENT_APP={app_name}", fg="green"))

        # --- Si se usa --shell, imprime los exports ---
        if shell:
            print(f'export SPLENT_APP={app_name}')
            print('source /workspace/.env')
            print('type set_prompt >/dev/null 2>&1 && set_prompt || true')
        else:
            click.echo(click.style("🌱 Environment updated successfully.", fg="cyan"))
            click.echo(click.style(f"📦 Active directory: {product_path}", fg="yellow"))

    except Exception as e:
        click.echo(click.style(f"❌ Failed to update environment: {e}", fg="red"))
