import click
from flask import current_app

from splent_cli.utils.decorators import requires_app
from splent_framework.managers.migration_manager import MigrationManager


@requires_app
@click.command(
    "db:status",
    short_help="Show migration status for all features.",
)
def db_status():
    app = current_app

    click.echo(click.style("\n📊 Migration Status\n", fg="cyan", bold=True))

    try:
        rows = MigrationManager.get_all_status(app)
    except Exception as e:
        click.echo(click.style(f"❌ Could not read splent_migrations: {e}", fg="red"))
        raise SystemExit(1)

    if not rows:
        click.echo(click.style("  (no features tracked yet)", fg="yellow"))
        return

    col_feat = max(len(r[0]) for r in rows)
    col_feat = max(col_feat, len("Feature"))

    click.echo(f"  {'Feature':<{col_feat}}  Last Migration")
    click.echo(f"  {'-' * col_feat}  {'-' * 40}")
    for feat, rev in rows:
        rev_display = rev or click.style("(none)", fg="yellow")
        click.echo(f"  {feat:<{col_feat}}  {rev_display}")

    click.echo()


cli_command = db_status
