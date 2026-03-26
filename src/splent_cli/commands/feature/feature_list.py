import os
import click
from splent_cli.services import context
from splent_framework.utils.pyproject_reader import PyprojectReader


@click.command(
    "feature:list", short_help="List all features declared in the active product."
)
def feature_list():
    """List features declared in [tool.splent] of the active product's pyproject.toml,
    grouped by environment (base, dev, prod)."""
    product = context.require_app()
    workspace = str(context.workspace())
    product_dir = os.path.join(workspace, product)

    try:
        reader = PyprojectReader.for_product(product_dir)
    except FileNotFoundError:
        click.secho("❌ pyproject.toml not found in product.", fg="red")
        raise SystemExit(1)

    base = reader.features
    dev = reader.features_dev
    prod = reader.features_prod

    if not base and not dev and not prod:
        click.secho("ℹ️  No features declared in pyproject.toml.", fg="yellow")
        return

    click.echo()

    groups = [
        ("features", base, "All environments"),
        ("features_dev", dev, "Dev only"),
        ("features_prod", prod, "Prod only"),
    ]

    for key, entries, label in groups:
        if entries:
            click.echo(click.style(f"  {label} — [tool.splent.{key}] ({len(entries)}):", bold=True))
            for entry in entries:
                click.echo(f"    - {entry}")
            click.echo()

    total = len(set(base + dev + prod))
    click.echo(click.style(f"  Total: {total} features", fg="bright_black"))
    click.echo()


cli_command = feature_list
