import click
from flask.cli import with_appcontext
from splent_cli.utils.dynamic_imports import get_app
from splent_framework.core.managers.feature_manager import FeatureManager


@click.command(
    "feature:list", help="Lists all feautures and those ignored by .featureignore."
)
@with_appcontext
def module_list():
    app = get_app
    manager = FeatureManager(app)

    loaded_modules, ignored_modules = manager.get_modules()

    click.echo(
        click.style(f"Loaded Features ({len(loaded_modules)}):", fg="green")
    )
    for module in loaded_modules:
        click.echo(f"- {module}")

    click.echo(
        click.style(
            f"\nIgnored Features ({len(ignored_modules)}):", fg="bright_yellow"
        )
    )
    for module in ignored_modules:
        click.echo(click.style(f"- {module}", fg="bright_yellow"))