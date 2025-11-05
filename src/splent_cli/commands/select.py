import os
import re
import click

@click.command("select", help="Select the active SPLENT app (updates .env and .splent_env)")
@click.argument("app_name", required=True)
def select_app(app_name):
    workspace_env_path = "/workspace/.env"
    splent_env_path = os.path.expanduser("~/.splent_env")
    product_path = os.path.join("/workspace", app_name)
    product_dotenv_path = os.path.join(product_path, "docker", ".env")

    # ❌ Salir antes de tocar nada
    if not os.path.exists(workspace_env_path):
        click.echo(click.style("❌ /workspace/.env does not exist.", fg="red"))
        return

    if not os.path.isdir(product_path):
        click.echo(click.style(f"❌ Product folder not found: {product_path}", fg="red"))
        return

    try:
        # ✅ Solo si todo es válido, entonces modificamos
        with open(workspace_env_path, "r") as f:
            content = f.read()

        pattern = re.compile(r"^SPLENT_APP=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"SPLENT_APP={app_name}", content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"SPLENT_APP={app_name}\n"

        with open(workspace_env_path, "w") as f:
            f.write(content)

        with open(splent_env_path, "w") as f:
            f.write(f'export SPLENT_APP={app_name}\n')
            f.write(f'export PS1="\\[\\e[1;32m\\]({app_name})\\[\\e[0m\\] \\w\\$ "\n')
            f.write('source /workspace/.env\n')

        click.echo(click.style(f"✅ SPLENT_APP set to '{app_name}'", fg="green"))
        click.echo(click.style("💡 Run 'source ~/.splent_env && source /workspace/.env' to apply changes.", fg="yellow"))


    except Exception as e:
        click.echo(click.style(f"❌ Failed to update: {e}", fg="red"))
