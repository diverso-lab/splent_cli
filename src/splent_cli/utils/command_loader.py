import os
import importlib
from splent_cli.utils.path_utils import PathUtils
import click


def load_commands(cli_group):
    commands_path = PathUtils.get_commands_path()

    for root, _, files in os.walk(commands_path):
        for file in files:
            if file.endswith(".py") and not file.startswith("__"):
                # Build the fully-qualified module name from the relative path
                rel_path = os.path.relpath(os.path.join(root, file), commands_path)
                module_name = (
                    "splent_cli.commands." + rel_path.replace(os.sep, ".")[:-3]
                )

                try:
                    module = importlib.import_module(module_name)
                except Exception as e:
                    if os.getenv("SPLENT_DEBUG"):
                        click.secho(f"⚠  Skipping {module_name}: {e}", fg="yellow", err=True)
                    continue

                # Prefer an explicit cli_command attribute when present
                if hasattr(module, "cli_command"):
                    command = getattr(module, "cli_command")
                    if isinstance(command, click.Command):
                        cli_group.add_command(command)
                        continue

                # Fall back to scanning all module attributes
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, click.Command):
                        cli_group.add_command(attr)
