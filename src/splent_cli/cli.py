import os
import sys
import click
from dotenv import load_dotenv

from splent_cli.utils.dynamic_imports import get_app
from splent_cli.utils.command_loader import load_commands

load_dotenv()


class SPLENTCLI(click.Group):
    """
    CLI principal que detecta si un comando requiere una app Flask y,
    en ese caso, inyecta el contexto automáticamente.
    """

    def invoke(self, ctx):
        cmd_name = ctx.protected_args[0] if ctx.protected_args else None
        command = self.get_command(ctx, cmd_name)

        if command and getattr(command, "requires_app", False):
            app = get_app()
            with app.app_context():
                return super().invoke(ctx)

        return super().invoke(ctx)


@click.group(cls=SPLENTCLI)
def cli():
    """SPLENT CLI: gestión unificada de features, productos y apps Flask."""


# Cargar todos los comandos automáticamente
load_commands(cli)


if __name__ == "__main__":
    cli()
