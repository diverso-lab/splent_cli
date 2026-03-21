import click
from flask import current_app

from splent_cli.utils.decorators import requires_app
from splent_framework.managers.migration_manager import MigrationManager


@requires_app
@click.command(
    "db:status",
    short_help="Show migration status for all features.",
    help=(
        "Display the last applied migration for every feature tracked in the "
        "splent_migrations table."
    ),
)
def db_status():
    app = current_app._get_current_object()

    rows = MigrationManager.get_all_status(app)

    click.echo("📋 Feature migration status:")
    click.echo(f"  {'Feature':<45} Last Migration")
    click.echo("  " + "─" * 65)

    if not rows:
        click.echo(
            click.style(
                "  No entries in splent_migrations. Run splent db:migrate to populate.",
                fg="yellow",
            )
        )
        return

    for feature, last_migration in rows:
        migration_str = (
            last_migration
            if last_migration
            else click.style("(none)", fg="yellow")
        )
        click.echo(f"  {feature:<45} {migration_str}")


cli_command = db_status
