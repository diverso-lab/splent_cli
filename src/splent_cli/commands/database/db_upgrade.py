import click
from flask import current_app
from flask_migrate import upgrade as alembic_upgrade

from splent_cli.utils.decorators import requires_app
from splent_framework.managers.migration_manager import MigrationManager


@requires_app
@click.command(
    "db:upgrade",
    short_help="Apply pending migrations (all features or a single one).",
)
@click.argument("feature", required=False, default=None)
def db_upgrade(feature):
    app = current_app

    if feature:
        dirs = {}
        mdir = MigrationManager.get_feature_migration_dir(feature)
        if not mdir:
            click.echo(
                click.style(
                    f"❌ No migrations directory found for '{feature}'.", fg="red"
                )
            )
            raise SystemExit(1)
        dirs[feature] = mdir
    else:
        dirs = MigrationManager.get_all_feature_migration_dirs()
        if not dirs:
            click.echo(
                click.style("⚠️  No feature migrations directories found.", fg="yellow")
            )
            return

    for feat, mdir in dirs.items():
        click.echo(click.style(f"  ⬆️  Applying migrations for {feat}...", fg="cyan"))
        try:
            alembic_upgrade(directory=mdir)
            revision = MigrationManager.get_current_feature_revision(
                feat, app.extensions["migrate"].db.engine
            )
            MigrationManager.update_feature_status(app, feat, revision)
            click.echo(click.style(f"  ✅ {feat} → {revision or 'head'}", fg="green"))
        except Exception as e:
            click.echo(click.style(f"  ❌ {feat}: {e}", fg="red"))


cli_command = db_upgrade
