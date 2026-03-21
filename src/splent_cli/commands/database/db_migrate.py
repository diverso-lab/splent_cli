import os

import click
from flask import current_app
from flask_migrate import migrate as alembic_migrate, upgrade as alembic_upgrade

from splent_cli.utils.decorators import requires_app
from splent_framework.db import db
from splent_framework.managers.migration_manager import MigrationManager


def _migrate_feature(app, feature_name: str, migrations_dir: str) -> bool:
    """Generate + apply migrations for one feature. Returns True on success."""
    click.echo(f"  ⚙️  Generating migrations for {feature_name}...")
    try:
        alembic_migrate(directory=migrations_dir, message=f"{feature_name}")
    except Exception as exc:
        # Alembic raises when there are no new changes — treat as non-fatal
        click.echo(click.style(f"  ℹ️  {exc}", fg="yellow"))

    click.echo(f"  ⬆️  Applying migrations for {feature_name}...")
    try:
        alembic_upgrade(directory=migrations_dir)
        revision = MigrationManager.get_current_feature_revision(feature_name, db.engine)
        MigrationManager.update_feature_status(app, feature_name, revision)
        click.echo(
            click.style(f"  ✅ {feature_name} → {revision or 'head'}", fg="green")
        )
        return True
    except Exception as exc:
        click.echo(click.style(f"  ❌ {feature_name}: {exc}", fg="red"))
        return False


@requires_app
@click.command(
    "db:migrate",
    help=(
        "Generate and apply database migrations.\n\n"
        "When FEATURE is given, only that feature's migrations/ directory is used.\n"
        "Without FEATURE, all features that have a migrations/ directory are processed."
    ),
)
@click.argument("feature", required=False)
def db_migrate(feature):
    app = current_app._get_current_object()

    if feature:
        migrations_dir = MigrationManager.get_feature_migration_dir(feature)
        if not migrations_dir:
            click.echo(
                click.style(
                    f"❌ Cannot resolve package for feature '{feature}'.", fg="red"
                )
            )
            return
        if not os.path.isdir(migrations_dir):
            click.echo(
                click.style(
                    f"❌ No migrations/ directory found for '{feature}'.\n"
                    f"   Expected path: {migrations_dir}\n"
                    f"   Run: splent feature:init-migrations {feature}",
                    fg="red",
                )
            )
            return
        _migrate_feature(app, feature, migrations_dir)
    else:
        feature_dirs = MigrationManager.get_all_feature_migration_dirs()
        if not feature_dirs:
            click.echo(
                click.style(
                    "⚠️  No feature migrations/ directories found. "
                    "Add migrations to your features first.",
                    fg="yellow",
                )
            )
            return
        click.echo(f"🔄 Migrating {len(feature_dirs)} feature(s)...")
        for feat_name, mdir in feature_dirs.items():
            _migrate_feature(app, feat_name, mdir)


cli_command = db_migrate
