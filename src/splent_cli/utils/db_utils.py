# splent_cli/utils/db_utils.py

import click


def check_db_connection(app) -> bool:
    """Try a lightweight DB ping inside the given Flask app context.

    Prints a clean error and returns False if the database is unreachable.
    Returns True if the connection succeeds.
    """
    try:
        from splent_framework.db import db
        with app.app_context():
            with db.engine.connect():
                pass
        return True
    except Exception as e:
        click.echo()
        click.secho("❌ Cannot connect to the database.", fg="red", bold=True)
        click.secho(f"   {e.__class__.__name__}: {e.args[0] if e.args else e}", fg="red")
        click.echo()
        click.secho(
            "   Make sure the product is running: splent product:up --dev",
            fg="yellow",
        )
        click.echo()
        return False
