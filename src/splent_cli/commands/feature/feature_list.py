import os
import click
from splent_cli.services import context
from splent_framework.utils.pyproject_reader import PyprojectReader


@click.command(
    "feature:list", short_help="Lists all optional dependencies declared in pyproject.toml."
)
def feature_list():
    """Lists all groups in [project.optional-dependencies] of the active product's pyproject.toml."""
    product = context.require_app()
    workspace = str(context.workspace())
    product_dir = os.path.join(workspace, product)

    try:
        reader = PyprojectReader.for_product(product_dir)
    except FileNotFoundError:
        click.secho("❌ pyproject.toml not found in product.", fg="red")
        raise SystemExit(1)

    groups = reader.optional_dependencies

    if not groups:
        click.secho("ℹ️  No optional dependencies declared in pyproject.toml.", fg="yellow")
        return

    for group, entries in groups.items():
        click.echo(click.style(f"\n{group} ({len(entries)}):", bold=True))
        if entries:
            for entry in entries:
                click.echo(f"  - {entry}")
        else:
            click.echo(click.style("  (empty)", fg="bright_black"))

    click.echo()


cli_command = feature_list
