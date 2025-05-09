import inspect
import os
import importlib
import click
import tomllib

from flask.cli import with_appcontext
from splent_cli.commands.db_reset import db_reset
from splent_cli.utils.path_utils import PathUtils
from splent_framework.core.seeders.BaseSeeder import BaseSeeder


def get_installed_seeders(specific_module=None):
    seeders = []

    pyproject_path = os.path.join(PathUtils.get_app_base_dir(), "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo(click.style(f"❌ pyproject.toml not found at {pyproject_path}", fg="red"))
        return seeders

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        features = data["project"]["optional-dependencies"].get("features", [])
    except Exception as e:
        click.echo(click.style(f"❌ Failed to read features from pyproject.toml: {e}", fg="red"))
        return seeders

    for feature in features:
        if specific_module and specific_module != feature.split("_")[-1]:
            continue

        try:
            seeder_module = importlib.import_module(f"{feature}.seeders")
            importlib.reload(seeder_module)

            for attr in dir(seeder_module):
                obj = getattr(seeder_module, attr)
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseSeeder)
                    and obj is not BaseSeeder
                ):
                    seeders.append(obj())
        except Exception as e:
            click.echo(
                click.style(f"❌ Error loading seeders from {feature}: {e}", fg="red"),
                err=True,
            )

    seeders.sort(key=lambda s: s.priority)
    return seeders


@click.command(
    "db:seed",
    help="Populates the database with the seeders defined in each feature.",
)
@click.option("--reset", is_flag=True, help="Reset the database before seeding.")
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Confirm the operation without prompting.",
)
@click.argument("module", required=False)
@with_appcontext
def db_seed(reset, yes, module):
    if reset:
        if yes or click.confirm(
            click.style(
                "This will reset the database, do you want to continue?",
                fg="red",
            ),
            abort=True,
        ):
            click.echo(click.style("Resetting the database...", fg="yellow"))
            ctx = click.get_current_context()
            ctx.invoke(db_reset, clear_migrations=False, yes=True)
        else:
            click.echo(click.style("Database reset cancelled.", fg="yellow"))
            return

    seeders = get_installed_seeders(specific_module=module)
    success = True

    if module:
        click.echo(click.style(f"Seeding data for the '{module}' feature...", fg="green"))
    else:
        click.echo(click.style("Seeding data for all features...", fg="green"))

    for seeder in seeders:
        try:
            seeder.run()
            click.echo(click.style(f"{seeder.__class__.__name__} performed.", fg="blue"))
        except Exception as e:
            click.echo(
                click.style(f"Error running {seeder.__class__.__name__}: {e}", fg="red")
            )
            click.echo(
                click.style("Rolling back session for safety.", fg="yellow")
            )
            success = False
            break

    if success:
        click.echo(click.style("Database populated with test data.", fg="green"))
