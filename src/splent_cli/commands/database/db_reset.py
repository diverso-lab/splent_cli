import os
import shutil
import click
from flask import current_app
from sqlalchemy import MetaData
from flask_migrate import stamp, init, migrate, upgrade

from splent_cli.utils.decorators import requires_app
from splent_framework.db import db
from splent_cli.commands.clear_uploads import clear_uploads
from splent_cli.utils.path_utils import PathUtils


@requires_app
@click.command(
    "db:reset",
    help="Resets the database, optionally clears migrations and recreates them.",
)
@click.option(
    "--clear-migrations",
    is_flag=True,
    help="Remove all tables including 'alembic_version', clear migrations folder, and recreate migrations.",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Confirm the operation without prompting.",
)
def db_reset(clear_migrations, yes):
    app = current_app

    if not yes and not click.confirm(
        "⚠️ WARNING: This will delete all data and clear uploads. Are you sure?",
        abort=True,
    ):
        return

    # --- STEP 1: Delete all table data
    trans = None
    try:
        meta = MetaData()
        meta.reflect(bind=db.engine)
        with db.engine.connect() as conn:
            trans = conn.begin()
            for table in reversed(meta.sorted_tables):
                if not clear_migrations or table.name != "alembic_version":
                    conn.execute(table.delete())
            trans.commit()

        click.echo(click.style("✅ All table data cleared.", fg="yellow"))

        # Stamp head (required to avoid alembic thinking migrations are missing)
        with app.app_context():
            stamp()

    except Exception as e:
        click.echo(click.style(f"❌ Error clearing table data: {e}", fg="red"))
        if trans:
            trans.rollback()
        return

    # --- STEP 2: Clear uploads
    ctx = click.get_current_context()
    ctx.invoke(clear_uploads)

    # --- STEP 3: Clear and recreate migrations
    if clear_migrations:
        migrations_dir = PathUtils.get_migrations_dir()
        if os.path.isdir(migrations_dir):
            shutil.rmtree(migrations_dir)
            click.echo(click.style("🧹 Migrations directory cleared.", fg="yellow"))

        try:
            with app.app_context():
                init()
                migrate()
                upgrade()

            click.echo(
                click.style("✅ Database recreated from new migrations.", fg="green")
            )

        except Exception as e:
            click.echo(click.style(f"❌ Error during migration reset: {e}", fg="red"))
            return

    click.echo(click.style("🎉 Database reset successfully.", fg="green"))


cli_command = db_reset
