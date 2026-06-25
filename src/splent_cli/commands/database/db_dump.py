import click
from dotenv import load_dotenv
import os
import tempfile
from datetime import datetime

from splent_cli.services import context
from splent_cli.utils.proc import run, require_tool


@click.command(
    "db:dump",
    short_help="Create a SQL dump of the MariaDB database.",
)
@click.argument("filename", required=False)
@context.requires_product
def db_dump(filename):
    load_dotenv()

    mariadb_hostname = os.getenv("MARIADB_HOSTNAME")
    mariadb_user = os.getenv("MARIADB_USER")
    mariadb_password = os.getenv("MARIADB_PASSWORD")
    mariadb_database = os.getenv("MARIADB_DATABASE")

    missing = [
        k
        for k, v in {
            "MARIADB_HOSTNAME": mariadb_hostname,
            "MARIADB_USER": mariadb_user,
            "MARIADB_PASSWORD": mariadb_password,
            "MARIADB_DATABASE": mariadb_database,
        }.items()
        if not v
    ]
    if missing:
        click.secho(f"❌ Missing env vars: {', '.join(missing)}", fg="red")
        raise SystemExit(1)

    # Generate default filename if not provided
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"dump_{timestamp}.sql"
    else:
        # Ensure filename has .sql extension
        if not filename.endswith(".sql"):
            filename += ".sql"

    require_tool(
        "mysqldump",
        "Install the MariaDB/MySQL client tools (provides 'mysqldump').",
    )

    env = {**os.environ, "MYSQL_PWD": mariadb_password or ""}

    # Dump to a temp file first and only replace the target on success, so a
    # pre-existing file with the same name is never truncated or deleted if the
    # dump fails midway.
    target_dir = os.path.dirname(os.path.abspath(filename)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".db_dump_", suffix=".sql.tmp", dir=target_dir
    )
    try:
        with os.fdopen(fd, "wb") as out:
            run(
                [
                    "mysqldump",
                    f"-h{mariadb_hostname}",
                    f"-u{mariadb_user}",
                    mariadb_database,
                ],
                stdout=out,
                text=False,
                env=env,
                tool_hint="Install the MariaDB/MySQL client tools (provides 'mysqldump').",
            )
        os.replace(tmp_path, filename)
        click.echo(
            click.style(f"Database dump created successfully: {filename}", fg="green")
        )
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


cli_command = db_dump
