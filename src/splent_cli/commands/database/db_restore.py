import gzip
import os
import shutil
import stat
import tempfile
import zlib

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
    Restore the database from FILENAME (a .sql or .sql.gz dump).

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

    restore_path = filename
    tmp_sql_path = None

    try:
        # Accept gzip-compressed dumps transparently. mysql needs a plain SQL
        # stream on stdin, so decompress to a temp file next to the dump and
        # feed that instead. Doing this before the pre-restore backup means a
        # corrupt archive aborts the command before anything else happens.
        # Truncated archives raise EOFError and corruption mid-stream raises
        # zlib.error; neither derives from OSError, so both are named here.
        if filename.endswith(".gz"):
            target_dir = os.path.dirname(os.path.abspath(filename)) or "."
            fd, tmp_sql_path = tempfile.mkstemp(
                prefix=".db_restore_", suffix=".sql.tmp", dir=target_dir
            )
            try:
                with os.fdopen(fd, "wb") as out, gzip.open(filename, "rb") as gz:
                    shutil.copyfileobj(gz, out)
            except (OSError, EOFError, zlib.error) as e:
                click.secho(
                    f"❌ Could not decompress '{filename}' ({e}).\n"
                    "The archive looks truncated or corrupt. "
                    "Nothing was written to the database.",
                    fg="red",
                )
                raise SystemExit(1)
            restore_path = tmp_sql_path

        # Take an automatic pre-restore backup so a corrupt/partial dump cannot
        # leave the live database half-overwritten with no way back.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"pre_restore_{database}_{timestamp}.sql"
        try:
            # 0600: the backup carries every password hash in the user table,
            # so it must not be created world-readable through the umask.
            backup_fd = os.open(
                backup_filename,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            with os.fdopen(backup_fd, "wb") as backup_out:
                run(
                    [
                        "mysqldump",
                        # Same reliability flags as db:dump, so the safety-net
                        # backup is itself a consistent, restorable dump.
                        "--single-transaction",
                        "--quick",
                        "--default-character-set=utf8mb4",
                        f"-h{host}",
                        f"-u{user}",
                        database,
                    ],
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
            with open(restore_path, "rb") as sql_file:
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
    finally:
        if tmp_sql_path and os.path.exists(tmp_sql_path):
            os.remove(tmp_sql_path)


cli_command = db_restore
