import click
from flask.cli import with_appcontext
from flask import current_app
from splent_framework.managers.feature_manager import FeatureManager


@click.command(
    "feature:list", help="Lists all feautures and those ignored by .featureignore."
)
@with_appcontext
def feature_list():
    app = current_app
    manager = FeatureManager(app)

    loaded_features, ignored_features = manager.get_features()

    click.echo(
        click.style(f"Loaded features ({len(loaded_features)}):", fg="green")
    )
    for feature in loaded_features:
        click.echo(f"- {feature}")

    click.echo(
        click.style(
            f"\nIgnored features ({len(ignored_features)}):", fg="bright_yellow"
        )
    )
    for feature in ignored_features:
        click.echo(click.style(f"- {feature}", fg="bright_yellow"))