import click
from flask import current_app
from splent_cli.utils.decorators import requires_app
from splent_framework.managers.feature_manager import FeatureManager


@requires_app
@click.command("feature:list", help="Lists all features declared in pyproject.toml.")
def feature_list():
    """Lists all features declared in the active product's pyproject.toml."""
    app = current_app
    manager = FeatureManager(app)

    try:
        features = manager.get_features()
    except Exception as e:
        click.echo(click.style(f"❌ Error reading features: {e}", fg="red"))
        return

    if not features:
        click.echo(click.style("ℹ️ No features declared in pyproject.toml.", fg="yellow"))
        return

    click.echo(click.style(f"Declared features ({len(features)}):", fg="green"))
    for f in features:
        click.echo(f"- {f}")


cli_command = feature_list
