import click
import subprocess
from dotenv import load_dotenv
import os

from splent_cli.services import context


@click.command(
    "db:console", short_help="Open a MariaDB console with credentials from .env."
)
@context.requires_product
def db_console():
    load_dotenv()

    mariadb_hostname = os.getenv("MARIADB_HOSTNAME")
    mariadb_user = os.getenv("MARIADB_USER")
    mariadb_password = os.getenv("MARIADB_PASSWORD")
    mariadb_database = os.getenv("MARIADB_DATABASE")

    missing = [
        name
        for name, val in {
            "MARIADB_HOSTNAME": mariadb_hostname,
            "MARIADB_USER": mariadb_user,
            "MARIADB_PASSWORD": mariadb_password,
            "MARIADB_DATABASE": mariadb_database,
        }.items()
        if not val
    ]
    if missing:
        click.secho("❌ Missing required environment variables:", fg="red")
        for var in missing:
            click.secho(f"   - {var}", fg="red")
        click.secho(
            "\n   Make sure the product .env is loaded: splent product:env --merge --dev",
            fg="yellow",
        )
        raise SystemExit(1)

    try:
        subprocess.run(
            [
                "mysql",
                f"-h{mariadb_hostname}",
                f"-u{mariadb_user}",
                f"-p{mariadb_password}",
                mariadb_database,
            ],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho(f"❌ Error opening MariaDB console: {e}", fg="red")


cli_command = db_console
