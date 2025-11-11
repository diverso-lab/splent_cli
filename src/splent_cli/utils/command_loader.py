import os
import importlib
from splent_cli.utils.path_utils import PathUtils
import click


def load_commands(cli_group):
    commands_path = PathUtils.get_commands_path()

    for root, _, files in os.walk(commands_path):
        for file in files:
            if file.endswith(".py") and not file.startswith("__"):
                # Construimos el nombre completo del módulo dinámicamente
                rel_path = os.path.relpath(os.path.join(root, file), commands_path)
                module_name = (
                    "splent_cli.commands." + rel_path.replace(os.sep, ".")[:-3]
                )

                try:
                    module = importlib.import_module(module_name)
                except Exception as e:
                    print(f"❌ Error al importar {module_name}: {e}")
                    continue

                # Preferencia por cli_command explícito
                if hasattr(module, "cli_command"):
                    command = getattr(module, "cli_command")
                    if isinstance(command, click.Command):
                        cli_group.add_command(command)
                        continue

                # Si no hay cli_command, inspeccionamos todo
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, click.Command):
                        cli_group.add_command(attr)
