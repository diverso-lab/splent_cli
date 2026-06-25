import os
import click
from datetime import datetime
from dotenv import load_dotenv

from splent_cli.services import context
from splent_cli.utils.proc import run


@click.command(
    "db:restore",
    short_help="Restore a MariaDB database from a SQL dump file.",
)
@click.argument("filename")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
@context.requires_product
def db_restore(filename, yes):
    """
    Restore the database from FILENAME (a .sql dump).

    Reads connection credentials from the workspace .env file
    (MARIADB_HOSTNAME, MARIADB_USER, MARIADB_PASSWORD, MARIADB_DATABASE).

    \b
    Example:
        splent db:restore dump_20250101_120000.sql
    """
    load_dotenv()

    if not os.path.exists(filename):
        click.secho(f"❌ File not found: {filename}", fg="red")
        raise SystemExit(1)

    host = os.getenv("MARIADB_HOSTNAME")
    user = os.getenv("MARIADB_USER")
    password = os.getenv("MARIADB_PASSWORD")
    database = os.getenv("MARIADB_DATABASE")

    missing = [
        k
        for k, v in {
            "MARIADB_HOSTNAME": host,
            "MARIADB_USER": user,
            "MARIADB_PASSWORD": password,
            "MARIADB_DATABASE": database,
        }.items()
        if not v
    ]

    if missing:
        click.secho(f"❌ Missing env vars: {', '.join(missing)}", fg="red")
        raise SystemExit(1)

    if not yes:
        click.secho(
            f"⚠️  This will overwrite the database '{database}' with the contents of '{filename}'.",
            fg="yellow",
        )
        if not click.confirm("Continue?"):
            click.echo("❎ Cancelled.")
            raise SystemExit(0)

    env = {**os.environ, "MYSQL_PWD": password or ""}

    # Take an automatic pre-restore backup so a corrupt/partial dump cannot
    # leave the live database half-overwritten with no way back.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"pre_restore_{database}_{timestamp}.sql"
    try:
        with open(backup_filename, "wb") as backup_out:
            run(
                ["mysqldump", f"-h{host}", f"-u{user}", database],
                stdout=backup_out,
                env=env,
                tool_hint=(
                    "Install the MariaDB/MySQL client tools (provides 'mysqldump'), "
                    "e.g. 'mysql-client' / 'mariadb-client'."
                ),
            )
    except click.ClickException:
        # mysqldump missing/failed (often a connection failure): do not proceed
        # to overwrite the live database without a safety net.
        if os.path.exists(backup_filename):
            os.remove(backup_filename)
        click.secho(
            f"❌ Could not create a pre-restore backup of '{database}' "
            f"(check the database is reachable at '{host}' and credentials are correct).\n"
            "Aborting before overwriting the live database.",
            fg="red",
        )
        raise SystemExit(1)

    click.secho(f"🛟 Pre-restore backup saved to: {backup_filename}", fg="cyan")

    try:
        with open(filename, "rb") as sql_file:
            run(
                ["mysql", f"-h{host}", f"-u{user}", database],
                stdin=sql_file,
                env=env,
                tool_hint=(
                    "Install the MariaDB/MySQL client tools (provides 'mysql'), "
                    "e.g. 'mysql-client' / 'mariadb-client'."
                ),
            )
        click.secho(f"✅ Database restored from: {filename}", fg="green")
    except click.ClickException as e:
        click.secho(f"❌ Error restoring database: {e.format_message()}", fg="red")
        click.secho(
            "The database may be in an inconsistent state. To roll back, restore "
            f"the pre-restore backup:\n  splent db:restore {backup_filename} --yes",
            fg="yellow",
        )
        raise SystemExit(1)


cli_command = db_restore
