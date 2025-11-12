import inspect
import importlib
import click

from splent_cli.commands.database.db_reset import db_reset
from splent_cli.utils.decorators import requires_app
from splent_cli.utils.feature_utils import get_features_from_pyproject
from splent_framework.seeders.BaseSeeder import BaseSeeder


def get_installed_seeders(specific_module=None):
    seeders = []

    features = get_features_from_pyproject()
    if not features:
        click.echo(click.style("⚠️  No features found in pyproject.toml", fg="yellow"))
        return seeders

    for feature in features:
        # Handle versioned feature names like "splent_feature_auth@v1.0.0"
        base_name = feature.split("@")[0]
        module_name = f"splent_io.{base_name}.seeders"

        if specific_module and not base_name.endswith(specific_module):
            continue

        try:
            seeder_module = importlib.import_module(module_name)
            importlib.reload(seeder_module)

            for attr in dir(seeder_module):
                obj = getattr(seeder_module, attr)
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseSeeder)
                    and obj is not BaseSeeder
                ):
                    seeders.append(obj())

        except ModuleNotFoundError:
            # feature simply has no seeders.py
            continue
        except Exception as e:
            click.echo(
                click.style(
                    f"❌ Error loading seeders from {module_name}: {e}", fg="red"
                ),
                err=True,
            )

    seeders.sort(key=lambda s: s.priority)
    return seeders


@requires_app
@click.command("db:seed", help="Populate the database using feature-level seeders.")
@click.option("--reset", is_flag=True, help="Reset the database before seeding.")
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompts.")
@click.argument("module", required=False)
def db_seed(reset, yes, module):
    if reset:
        if yes or click.confirm(
            click.style("⚠️  This will reset the database. Continue?", fg="red"),
            abort=True,
        ):
            click.echo(click.style("🔄 Resetting the database...", fg="yellow"))
            ctx = click.get_current_context()
            ctx.invoke(db_reset, clear_migrations=False, yes=True)
        else:
            click.echo(click.style("❌ Database reset cancelled.", fg="yellow"))
            return

    seeders = get_installed_seeders(specific_module=module)
    if not seeders:
        click.echo(click.style("⚠️  No seeders found.", fg="yellow"))
        return

    click.echo(
        click.style(
            f"🌱 Seeding {'feature ' + module if module else 'all features'}...",
            fg="green",
        )
    )

    success = True
    for seeder in seeders:
        try:
            seeder.run()
            click.echo(
                click.style(f"✔ {seeder.__class__.__name__} completed.", fg="blue")
            )
        except Exception as e:
            click.echo(
                click.style(f"❌ Error in {seeder.__class__.__name__}: {e}", fg="red")
            )
            success = False
            break

    if success:
        click.echo(click.style("✅ Database successfully populated.", fg="green"))


cli_command = db_seed
