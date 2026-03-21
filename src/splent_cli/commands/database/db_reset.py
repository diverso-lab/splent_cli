import os
import click
from flask import current_app
from sqlalchemy import MetaData, text
from flask_migrate import upgrade as alembic_upgrade

from splent_cli.utils.decorators import requires_app
from splent_framework.db import db
from splent_cli.commands.clear_uploads import clear_uploads
from splent_framework.managers.migration_manager import (
    MigrationManager,
    SPLENT_MIGRATIONS_TABLE,
)


@requires_app
@click.command(
    "db:reset",
    short_help="Resets the database, optionally clears feature migration scripts and recreates them.",
)
@click.option(
    "--clear-migrations",
    is_flag=True,
    help=(
        "Delete all migration scripts from every feature's migrations/versions/ "
        "directory and regenerate them from scratch."
    ),
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Confirm the operation without prompting.",
)
def db_reset(clear_migrations, yes):
    app = current_app._get_current_object()

    if not yes and not click.confirm(
        "⚠️ WARNING: This will delete all data and clear uploads. Are you sure?",
        abort=True,
    ):
        return

    # --- STEP 1: Drop all user table data (keep tracking tables for last)
    trans = None
    try:
        meta = MetaData()
        meta.reflect(bind=db.engine)

        # Tables to preserve during data wipe (tracking tables, recreated later)
        tracking = {SPLENT_MIGRATIONS_TABLE}
        if not clear_migrations:
            # Keep alembic_<feature> tables so Alembic still knows current state
            tracking |= {t.name for t in meta.sorted_tables if t.name.startswith("alembic_")}

        with db.engine.connect() as conn:
            trans = conn.begin()
            for table in reversed(meta.sorted_tables):
                if table.name not in tracking:
                    conn.execute(table.delete())
            trans.commit()

        click.echo(click.style("✅ All table data cleared.", fg="yellow"))

    except Exception as exc:
        click.echo(click.style(f"❌ Error clearing table data: {exc}", fg="red"))
        if trans:
            trans.rollback()
        return

    # --- STEP 2: Clear uploads
    ctx = click.get_current_context()
    ctx.invoke(clear_uploads)

    # --- STEP 3: Optionally wipe and regenerate feature migration scripts
    if clear_migrations:
        _clear_feature_migration_scripts()
        _wipe_migration_tracking(app)
        _regenerate_feature_migrations(app)
    else:
        # Just re-apply pending migrations to restore schema
        click.echo("⬆️  Re-applying feature migrations...")
        feature_dirs = MigrationManager.get_all_feature_migration_dirs()
        for feat_name, mdir in feature_dirs.items():
            try:
                alembic_upgrade(directory=mdir)
                revision = MigrationManager.get_current_feature_revision(feat_name, db.engine)
                MigrationManager.update_feature_status(app, feat_name, revision)
                click.echo(click.style(f"  ✅ {feat_name} → {revision or 'head'}", fg="green"))
            except Exception as exc:
                click.echo(click.style(f"  ❌ {feat_name}: {exc}", fg="red"))

    click.echo(click.style("🎉 Database reset successfully.", fg="green"))


def _clear_feature_migration_scripts():
    """Delete all .py files from every feature's migrations/versions/ directory."""
    feature_dirs = MigrationManager.get_all_feature_migration_dirs()
    for feat_name, mdir in feature_dirs.items():
        versions_dir = os.path.join(mdir, "versions")
        if not os.path.isdir(versions_dir):
            continue
        removed = 0
        for f in os.listdir(versions_dir):
            if f.endswith(".py"):
                os.remove(os.path.join(versions_dir, f))
                removed += 1
        if removed:
            click.echo(
                click.style(f"🧹 {feat_name}: removed {removed} migration script(s).", fg="yellow")
            )


def _wipe_migration_tracking(app):
    """Drop all alembic_<feature> version tables and clear splent_migrations."""
    with db.engine.connect() as conn:
        meta = MetaData()
        meta.reflect(bind=db.engine)
        with db.engine.begin() as conn2:
            for table in meta.sorted_tables:
                if table.name.startswith("alembic_"):
                    conn2.execute(text(f"DROP TABLE IF EXISTS `{table.name}`"))
                    click.echo(
                        click.style(f"🗑  Dropped tracking table: {table.name}", fg="yellow")
                    )
            conn2.execute(text(f"DELETE FROM `{SPLENT_MIGRATIONS_TABLE}`"))
    click.echo(click.style("🧹 Migration tracking cleared.", fg="yellow"))


def _regenerate_feature_migrations(app):
    """Generate and apply fresh migrations for all features."""
    from flask_migrate import migrate as alembic_migrate

    feature_dirs = MigrationManager.get_all_feature_migration_dirs()
    if not feature_dirs:
        click.echo(click.style("⚠️  No feature migrations/ directories found.", fg="yellow"))
        return

    for feat_name, mdir in feature_dirs.items():
        click.echo(f"  ⚙️  Generating migrations for {feat_name}...")
        try:
            alembic_migrate(directory=mdir, message=f"{feat_name} initial")
        except Exception as exc:
            click.echo(click.style(f"  ℹ️  {exc}", fg="yellow"))

        try:
            alembic_upgrade(directory=mdir)
            revision = MigrationManager.get_current_feature_revision(feat_name, db.engine)
            MigrationManager.update_feature_status(app, feat_name, revision)
            click.echo(click.style(f"  ✅ {feat_name} → {revision or 'head'}", fg="green"))
        except Exception as exc:
            click.echo(click.style(f"  ❌ {feat_name}: {exc}", fg="red"))


cli_command = db_reset
