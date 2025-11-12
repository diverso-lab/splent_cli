# splent_cli/commands/command_create.py

import os
import click
from splent_cli.utils.path_utils import PathUtils


@click.command("command:create", help="Creates a new CLI command skeleton.")
@click.argument("name")
def command_create(name):
    commands_dir = PathUtils.get_commands_path()
    filename = f"{name.lower().replace(':', '_')}.py"
    filepath = os.path.join(commands_dir, filename)

    if os.path.exists(filepath):
        click.echo(
            click.style(
                f"❌ The command '{name}' already exists as file {filename}.", fg="red"
            )
        )
        return

    use_flask = click.confirm(
        "Does this command require access to the Flask app context?", default=False
    )

    command_var = name.lower().replace(":", "_")
    command_name = name.lower()

    if use_flask:
        template = f'''import click
from flask import current_app
from splent_cli.utils.decorators import requires_app


@requires_app
@click.command("{command_name}", help="Briefly describe what this command does.")
def {command_var}():
    app = current_app
    # TODO: Implement your command logic here
    pass

cli_command = {command_var}
'''
    else:
        template = f'''import click


@click.command("{command_name}", help="Briefly describe what this command does.")
def {command_var}():
    # TODO: Implement your command logic here
    pass

cli_command = {command_var}
'''

    with open(filepath, "w") as f:
        f.write(template)

    click.echo(click.style(f"✅ Command '{name}' created at: {filepath}", fg="green"))
