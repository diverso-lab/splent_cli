import click
from flask import current_app
from flask_migrate import upgrade as alembic_upgrade
from sqlalchemy import text, MetaData

from splent_cli.utils.decorators import requires_db
from splent_framework.db import db
from splent_framework.managers.migration_manager import (
    MigrationManager,
    SPLENT_MIGRATIONS_TABLE,
)
from splent_cli.commands.clear_uploads import clear_uploads


@requires_db
@click.command(
    "db:reset",
    short_help="Drop all tables and re-apply migrations from scratch.",
)
@click.option("-y", "--yes", is_flag=True, help="Confirm without prompting.")
def db_reset(yes):
    """
    Full database reset: drops ALL tables (data, alembic tracking,
    splent_migrations), then re-applies existing feature migrations.

    Feature migration files are NOT deleted — only the database is wiped.
    """
    app = current_app

    if not yes and not click.confirm(
        "⚠️  WARNING: This will DROP all tables and clear uploads. Are you sure?",
        abort=True,
    ):
        return

    # --- STEP 1: Drop ALL tables (including alembic_* and splent_migrations) ---
    click.echo(click.style("🗑️  Dropping all tables...", fg="yellow"))
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            meta = MetaData()
            meta.reflect(bind=db.engine)
            for table in meta.sorted_tables:
                conn.execute(text(f"DROP TABLE IF EXISTS `{table.name}`"))
                click.echo(click.style(f"   Dropped {table.name}", fg="bright_black"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
            conn.commit()
        click.echo(click.style("✅ All tables dropped.", fg="yellow"))
    except Exception as e:
        click.echo(click.style(f"❌ Error dropping tables: {e}", fg="red"))
        return

    # --- STEP 2: Recreate splent_migrations tracking table ---
    click.echo(click.style("📋 Recreating splent_migrations table...", fg="cyan"))
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{SPLENT_MIGRATIONS_TABLE}` (
                        `feature`        VARCHAR(255) NOT NULL,
                        `last_migration` VARCHAR(255) DEFAULT NULL,
                        PRIMARY KEY (`feature`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
            )
    except Exception as e:
        click.echo(click.style(f"❌ Error creating tracking table: {e}", fg="red"))
        return

    # --- STEP 3: Clear uploads ---
    ctx = click.get_current_context()
    ctx.invoke(clear_uploads)

    # --- STEP 4: Re-apply all existing feature migrations ---
    dirs = MigrationManager.get_all_feature_migration_dirs()
    if not dirs:
        click.echo(click.style("⚠️  No feature migrations found.", fg="yellow"))
    else:
        click.echo(click.style(f"⬆️  Applying migrations for {len(dirs)} features...", fg="cyan"))
        for feat, mdir in dirs.items():
            try:
                alembic_upgrade(directory=mdir)
                revision = MigrationManager.get_current_feature_revision(
                    feat, db.engine
                )
                MigrationManager.update_feature_status(app, feat, revision)
                click.echo(
                    click.style(f"  ✅ {feat} → {revision or 'head'}", fg="green")
                )
            except Exception as e:
                click.echo(click.style(f"  ❌ {feat}: {e}", fg="red"))

    click.echo(click.style("\n🎉 Database reset complete.", fg="green"))


cli_command = db_reset
