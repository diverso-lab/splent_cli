import os
import sys
import importlib
import click
from dotenv import load_dotenv
from flask.cli import FlaskGroup
from splent_cli.utils.dynamic_imports import get_app

from splent_cli.utils.path_utils import PathUtils

load_dotenv()


def check_working_dir():
    working_dir = os.getenv("WORKING_DIR", "").strip()

    if working_dir != "/workspace":
        print(f"❌ CLI ERROR: WORKING_DIR must be set to '/workspace', but got '{working_dir}'.")
        sys.exit(1)


class SPLENTCLI(FlaskGroup):
    def __init__(self, **kwargs):
        super().__init__(create_app=get_app, **kwargs)

    def get_command(self, ctx, cmd_name):
        rv = super().get_command(ctx, cmd_name)
        if rv is None:
            click.echo(f"No such command '{cmd_name}'.")
            click.echo("Try 'splent --help' for a list of available commands.")
        return rv


def load_commands(cli_group):
    """
    Dynamically import all commands in the specified directory and add them to the CLI group.
    """
    commands_path = PathUtils.get_commands_path()

    for file in os.listdir(commands_path):
        if file.endswith(".py") and not file.startswith("__"):
            module_name = f"splent_cli.commands.{file[:-3]}"
            module = importlib.import_module(module_name)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, click.Command):
                    cli_group.add_command(attr)


@click.group(cls=SPLENTCLI)
def cli():
    """A CLI tool to help with project development."""


if __name__ == "__main__":
    cli()
