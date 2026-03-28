"""
product:test — Run all feature tests for a product.

Can be used in detached mode (with product name argument) or with the
active product. Designed for batch testing across multiple products.
"""

import os
import subprocess
import sys

import click
import tomllib

from splent_cli.services import context
from splent_cli.utils.feature_utils import read_features_from_data


@click.command(
    "product:test",
    short_help="Run all feature tests for a product.",
)
@click.argument("product_name", required=False)
@click.option("--unit", "level_unit", is_flag=True, help="Run unit tests only.")
@click.option("--integration", "level_integration", is_flag=True, help="Run integration tests only.")
@click.option("--functional", "level_functional", is_flag=True, help="Run functional tests only.")
@click.option("-v", "verbose", is_flag=True, help="Verbose output.")
def product_test(product_name, level_unit, level_integration, level_functional, verbose):
    """Run all feature tests for a product.

    \b
    Requires detached mode (no SPLENT_APP selected):
        splent product:test sample_splent_app --unit

    \b
    This is a convenience wrapper around `splent feature:test` that
    tests ALL features declared in the product's pyproject.toml.
    Designed for batch testing across multiple products.
    """
    if not context.is_detached():
        click.secho(
            f"❌ A product is currently selected: {context.active_app()}\n"
            f"   product:test requires detached mode.\n"
            f"   Run: splent product:deselect\n"
            f"   Or use: splent feature:test --unit",
            fg="red",
        )
        raise SystemExit(1)

    product = product_name
    if not product:
        click.secho(
            "❌ No product specified.\n"
            "   Usage: splent product:test <product_name> [--unit|--integration|--functional]",
            fg="red",
        )
        raise SystemExit(1)

    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.isfile(pyproject_path):
        click.secho(f"❌ Product not found: {product}", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    env = os.getenv("SPLENT_ENV", "dev")
    features = read_features_from_data(data, env)

    if not features:
        click.secho("ℹ️  No features declared.", fg="yellow")
        return

    click.echo(
        click.style(
            f"\n🧪 product:test — {product} ({len(features)} features)\n",
            bold=True,
        )
    )

    # Build splent feature:test command
    cmd = ["splent", "feature:test"]

    # Level flags
    levels = []
    if level_unit:
        levels.append("--unit")
    if level_integration:
        levels.append("--integration")
    if level_functional:
        levels.append("--functional")
    # Default: unit + integration + functional (same as feature:test default)

    cmd.extend(levels)
    if verbose:
        cmd.append("-v")

    # Set SPLENT_APP for the subprocess if running in detached mode
    env_vars = dict(os.environ)
    env_vars["SPLENT_APP"] = product

    result = subprocess.run(cmd, env=env_vars)

    if result.returncode != 0:
        click.secho(f"\n❌ product:test failed for {product}.", fg="red")
        raise SystemExit(result.returncode)

    click.secho(f"\n✅ product:test passed for {product}.", fg="green")


cli_command = product_test
