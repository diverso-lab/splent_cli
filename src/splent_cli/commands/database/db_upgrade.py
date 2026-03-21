import os

import click
from flask import current_app
from flask_migrate import upgrade as alembic_upgrade

from splent_cli.utils.decorators import requires_app
from splent_framework.db import db
from splent_framework.managers.migration_manager import MigrationManager


@requires_app
@click.command(
    "db:upgrade",
    help=(
        "Apply pending migrations.\n\n"
        "When FEATURE is given, only that feature's pending migrations are applied.\n"
        "Without FEATURE, all features that have a migrations/ directory are upgraded."
    ),
)
@click.argument("feature", required=False)
def db_upgrade(feature):
    app = current_app._get_current_object()

    def _upgrade(feat_name: str, migrations_dir: str) -> None:
        click.echo(f"  ⬆️  Upgrading {feat_name}...")
        try:
            alembic_upgrade(directory=migrations_dir)
            revision = MigrationManager.get_current_feature_revision(feat_name, db.engine)
            MigrationManager.update_feature_status(app, feat_name, revision)
            click.echo(
                click.style(f"  ✅ {feat_name} → {revision or 'head'}", fg="green")
            )
        except Exception as exc:
            click.echo(click.style(f"  ❌ {feat_name}: {exc}", fg="red"))

    if feature:
        migrations_dir = MigrationManager.get_feature_migration_dir(feature)
        if not migrations_dir or not os.path.isdir(migrations_dir):
            click.echo(
                click.style(
                    f"❌ No migrations/ directory found for '{feature}'.", fg="red"
                )
            )
            return
        _upgrade(feature, migrations_dir)
    else:
        feature_dirs = MigrationManager.get_all_feature_migration_dirs()
        if not feature_dirs:
            click.echo(
                click.style("⚠️  No feature migrations/ directories found.", fg="yellow")
            )
            return
        click.echo(f"⬆️  Upgrading {len(feature_dirs)} feature(s)...")
        for feat_name, mdir in feature_dirs.items():
            _upgrade(feat_name, mdir)


cli_command = db_upgrade
