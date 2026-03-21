import os

import click
from flask import current_app
from flask_migrate import downgrade as alembic_downgrade

from splent_cli.utils.decorators import requires_app
from splent_framework.db import db
from splent_framework.managers.migration_manager import MigrationManager


@requires_app
@click.command(
    "db:rollback",
    help="Roll back migrations for a specific feature.",
)
@click.argument("feature")
@click.option(
    "--steps",
    default=1,
    show_default=True,
    help="Number of migration steps to roll back.",
)
def db_rollback(feature, steps):
    app = current_app._get_current_object()

    migrations_dir = MigrationManager.get_feature_migration_dir(feature)
    if not migrations_dir or not os.path.isdir(migrations_dir):
        click.echo(
            click.style(
                f"❌ No migrations/ directory found for '{feature}'.", fg="red"
            )
        )
        return

    revision_target = f"-{steps}"
    click.echo(f"⬇️  Rolling back {feature} by {steps} step(s)...")
    try:
        alembic_downgrade(directory=migrations_dir, revision=revision_target)
        revision = MigrationManager.get_current_feature_revision(feature, db.engine)
        MigrationManager.update_feature_status(app, feature, revision)
        click.echo(
            click.style(
                f"✅ {feature} rolled back → {revision or 'base'}", fg="green"
            )
        )
    except Exception as exc:
        click.echo(click.style(f"❌ {feature}: {exc}", fg="red"))


cli_command = db_rollback
