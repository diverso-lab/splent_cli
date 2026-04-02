import click
import subprocess
from dotenv import load_dotenv
import os
from datetime import datetime

from splent_cli.services import context


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

    try:
        env = {**os.environ, "MYSQL_PWD": mariadb_password or ""}
        with open(filename, "wb") as out:
            subprocess.run(
                [
                    "mysqldump",
                    f"-h{mariadb_hostname}",
                    f"-u{mariadb_user}",
                    mariadb_database,
                ],
                stdout=out,
                check=True,
                env=env,
            )
        click.echo(
            click.style(f"Database dump created successfully: {filename}", fg="green")
        )
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"Error creating database dump: {e}", fg="red"))
        if os.path.exists(filename):
            os.remove(filename)
            click.echo(click.style(f"Partial file removed: {filename}", fg="yellow"))


cli_command = db_dump
