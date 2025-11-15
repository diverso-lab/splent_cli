import click
from dotenv import load_dotenv

from splent_cli.utils.dynamic_imports import get_app
from splent_cli.utils.command_loader import load_commands

load_dotenv()


class SPLENTCLI(click.Group):
    """
    Main SPLENT CLI class.

    - Automatically injects the Flask app context for commands marked with `requires_app = True`.
    - Displays commands grouped by category for a cleaner, more readable help output.
    """

    def invoke(self, ctx):
        cmd_name = ctx.protected_args[0] if ctx.protected_args else None
        command = self.get_command(ctx, cmd_name)

        if command and getattr(command, "requires_app", False):
            app = get_app()
            with app.app_context():
                return super().invoke(ctx)

        return super().invoke(ctx)

    def format_commands(self, ctx, formatter):
        """Group SPLENT commands by category in the CLI help output."""
        groups = {
            "🌿 Feature Management": [
                cmd for cmd in self.commands if cmd.startswith("feature:")
            ],
            "🏗️  Product Management": [
                cmd for cmd in self.commands if cmd.startswith("product:")
            ],
            "🧱 Database": [cmd for cmd in self.commands if cmd.startswith("db:")],
            "🧰 Utilities": [
                cmd
                for cmd in self.commands
                if cmd.startswith(
                    ("clear:", "env", "select", "info", "version", "doctor")
                )
            ],
            "🐍 Development & QA": [
                cmd
                for cmd in self.commands
                if cmd.startswith(("linter", "test", "coverage", "locust"))
            ],
            "⚙️ Build & Assets": [
                cmd for cmd in self.commands if cmd.startswith("webpack:")
            ],
        }

        for title, cmds in groups.items():
            cmds = [c for c in cmds if c in self.commands]
            if not cmds:
                continue

            with formatter.section(title):
                rows = []
                for cmd_name in sorted(cmds):
                    cmd = self.get_command(ctx, cmd_name)
                    if cmd is None or cmd.hidden:
                        continue
                    rows.append((cmd_name, cmd.get_short_help_str()))
                if rows:
                    formatter.write_dl(rows)


@click.group(cls=SPLENTCLI)
def cli():
    """Command-line interface for managing SPLENT products, features, environments, and development workflows."""
    pass


# Automatically load all command modules
load_commands(cli)


if __name__ == "__main__":
    cli()
