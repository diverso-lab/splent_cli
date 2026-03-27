import os
import click
import subprocess
from dotenv import load_dotenv


@click.command(
    "db:restore",
    short_help="Restore a MariaDB database from a SQL dump file.",
)
@click.argument("filename")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
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

    try:
        env = {**os.environ, "MYSQL_PWD": password or ""}
        with open(filename, "rb") as sql_file:
            subprocess.run(
                ["mysql", f"-h{host}", f"-u{user}", database],
                stdin=sql_file,
                check=True,
                env=env,
            )
        click.secho(f"✅ Database restored from: {filename}", fg="green")
    except subprocess.CalledProcessError as e:
        click.secho(f"❌ Error restoring database: {e}", fg="red")
        raise SystemExit(1)


cli_command = db_restore
