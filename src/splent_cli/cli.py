import os

import click
from dotenv import load_dotenv

from splent_cli.utils.dynamic_imports import get_app
from splent_cli.utils.command_loader import load_commands
from splent_cli.utils.db_utils import check_db_connection

load_dotenv()


class SPLENTCLI(click.Group):
    """
    Main SPLENT CLI class.

    - Automatically injects the Flask app context for commands marked with `requires_app = True`.
    - Checks DB connectivity for commands marked with `requires_db = True`.
    - Discovers CLI commands contributed by features via ``app.extensions["splent_feature_commands"]``.
    - Displays commands grouped by category for a cleaner, more readable help output.
    """

    # ── Feature-contributed commands ──────────────────────────────

    def _load_feature_commands(self) -> dict[str, click.BaseCommand]:
        """Return ``{"feature:<name>": click.Group, ...}`` from the Flask app.

        Each feature that defines CLI commands gets a Click Group registered
        as ``feature:<short_name>``.  Individual commands become subcommands::

            splent feature:mail config
            splent feature:mail check --to user@example.com

        Commands are only available when a product is active and the app can
        be built.  On any failure the result is an empty dict — built-in CLI
        commands are never affected.
        """
        if hasattr(self, "_feature_cmds_cache"):
            return self._feature_cmds_cache

        self._feature_cmds_cache: dict[str, click.BaseCommand] = {}
        try:
            app = get_app()
            with app.app_context():
                registry = app.extensions.get("splent_feature_commands", {})
                for feature_short, commands in registry.items():
                    group = click.Group(
                        name=f"feature:{feature_short}",
                        help=f"Commands contributed by splent_feature_{feature_short}.",
                    )
                    group.requires_app = True  # type: ignore[attr-defined]
                    for cmd in commands:
                        group.add_command(cmd)
                    self._feature_cmds_cache[group.name] = group
        except Exception as e:
            if os.getenv("SPLENT_DEBUG"):
                click.secho(f"  ⚠ Feature commands not loaded: {e}", fg="yellow", err=True)
        return self._feature_cmds_cache

    def get_command(self, ctx, cmd_name):
        # Built-in commands take priority
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        # Fall back to feature-contributed command groups
        feat_cmds = self._load_feature_commands()
        return feat_cmds.get(cmd_name)

    def list_commands(self, ctx):
        builtin = super().list_commands(ctx)
        feat = sorted(self._load_feature_commands().keys())
        return builtin + feat

    # ── App context injection ─────────────────────────────────────

    def invoke(self, ctx):
        cmd_name = ctx.protected_args[0] if ctx.protected_args else None
        command = self.get_command(ctx, cmd_name)

        if command and getattr(command, "requires_app", False):
            app = get_app()
            if getattr(command, "requires_db", False):
                if not check_db_connection(app):
                    raise SystemExit(1)
            with app.app_context():
                return super().invoke(ctx)

        return super().invoke(ctx)

    def format_commands(self, ctx, formatter):
        """Group SPLENT commands by category in the CLI help output."""
        all_cmds = self.list_commands(ctx)
        groups = {
            "🌿 Feature Management": [
                cmd for cmd in all_cmds
                if cmd.startswith("feature:") and cmd not in self._load_feature_commands()
            ],
            "🏗️ Product Management": [
                cmd for cmd in all_cmds if cmd.startswith("product:")
            ],
            "🧬 SPL & Variability": [
                cmd for cmd in all_cmds if cmd.startswith(("spl:", "uvl:"))
            ],
            "🧱 Database": [cmd for cmd in all_cmds if cmd.startswith("db:")],
            "💾 Cache": [cmd for cmd in all_cmds if cmd.startswith("cache:")],
            "🧰 Utilities": [
                cmd
                for cmd in all_cmds
                if cmd.startswith(
                    ("clear:", "env", "select", "info", "version", "doctor", "tokens")
                )
            ],
            "🐍 Development & QA": [
                cmd
                for cmd in all_cmds
                if cmd.startswith(("linter", "test", "coverage", "locust"))
            ],
            "🔌 Feature Commands": [
                cmd for cmd in all_cmds
                if cmd in self._load_feature_commands()
            ],
        }
        for title, cmds in groups.items():
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
