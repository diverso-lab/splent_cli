import os
import click
from flask import current_app
from flask_migrate import upgrade as alembic_upgrade, migrate as alembic_migrate
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
    short_help="Resets the database, optionally clears migrations and recreates them.",
)
@click.option(
    "--clear-migrations",
    is_flag=True,
    help="Drop alembic_<feature> tables, wipe versions/, clear splent_migrations, and regenerate.",
)
@click.option("-y", "--yes", is_flag=True, help="Confirm without prompting.")
def db_reset(clear_migrations, yes):
    app = current_app

    if not yes and not click.confirm(
        "⚠️  WARNING: This will delete all data and clear uploads. Are you sure?",
        abort=True,
    ):
        return

    # --- STEP 1: Drop all table data (keep alembic tracking tables) ---
    try:
        meta = MetaData()
        meta.reflect(bind=db.engine)
        with db.engine.connect() as conn:
            trans = conn.begin()
            for table in reversed(meta.sorted_tables):
                skip = (
                    table.name.startswith("alembic_")
                    or table.name == SPLENT_MIGRATIONS_TABLE
                )
                if not skip:
                    conn.execute(table.delete())
            trans.commit()
        click.echo(click.style("✅ All table data cleared.", fg="yellow"))
    except Exception as e:
        click.echo(click.style(f"❌ Error clearing table data: {e}", fg="red"))
        return

    # --- STEP 2: Clear uploads ---
    ctx = click.get_current_context()
    ctx.invoke(clear_uploads)

    # --- STEP 3: Per-feature migration reset ---
    dirs = MigrationManager.get_all_feature_migration_dirs()

    if clear_migrations:
        # Drop alembic_<feature> tables and wipe versions/
        with db.engine.connect() as conn:
            trans = conn.begin()
            for feat in dirs:
                try:
                    conn.execute(text(f"DROP TABLE IF EXISTS `alembic_{feat}`"))
                    click.echo(click.style(f"🗑️  Dropped alembic_{feat}", fg="yellow"))
                except Exception:
                    pass
            # Clear splent_migrations
            try:
                conn.execute(text(f"DELETE FROM `{SPLENT_MIGRATIONS_TABLE}`"))
            except Exception:
                pass
            trans.commit()

        for feat, mdir in dirs.items():
            versions_dir = os.path.join(mdir, "versions")
            if os.path.isdir(versions_dir):
                for f in os.listdir(versions_dir):
                    if f.endswith(".py"):
                        os.remove(os.path.join(versions_dir, f))
                click.echo(click.style(f"🧹 Cleared versions/ for {feat}", fg="yellow"))

        # Regenerate
        for feat, mdir in dirs.items():
            click.echo(click.style(f"  ⚙️  Regenerating {feat}...", fg="cyan"))
            try:
                alembic_migrate(directory=mdir, message=feat)
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
    else:
        # Just re-apply existing migrations
        for feat, mdir in dirs.items():
            click.echo(click.style(f"  ⬆️  Re-applying {feat}...", fg="cyan"))
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

    click.echo(click.style("🎉 Database reset successfully.", fg="green"))


cli_command = db_reset
