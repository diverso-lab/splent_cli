"""
product:test — Run all feature tests for a product.

Works in both modes:
  - Attached: tests the active product (or override with --product).
  - Detached: requires --product.

Multiple --product flags can be passed to test several products in a single run.
"""

import os
import subprocess

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import load_product_features


def _run_product_tests(product, workspace, levels, verbose):
    """Run feature:test for a single product. Returns the exit code."""
    product_path = os.path.join(workspace, product)

    try:
        features = load_product_features(product_path, os.getenv("SPLENT_ENV", "dev"))
    except FileNotFoundError:
        click.secho(f"❌ Product not found: {product}", fg="red")
        return 1

    if not features:
        click.secho(f"  ℹ️  {product}: no features declared.", fg="yellow")
        return 0

    click.secho(
        f"\n  ▶  {product}  ({len(features)} features)",
        fg="cyan",
        bold=True,
    )

    cmd = ["splent", "feature:test"]
    cmd.extend(levels)
    if verbose:
        cmd.append("-v")

    env_vars = dict(os.environ)
    env_vars["SPLENT_APP"] = product

    result = subprocess.run(cmd, env=env_vars)
    return result.returncode


@click.command(
    "product:test",
    short_help="Run all feature tests for a product.",
)
@click.option(
    "--product",
    "products",
    multiple=True,
    help="Product(s) to test. Can be repeated. Defaults to active product.",
)
@click.option("--unit", "level_unit", is_flag=True, help="Run unit tests only.")
@click.option(
    "--integration",
    "level_integration",
    is_flag=True,
    help="Run integration tests only.",
)
@click.option(
    "--functional", "level_functional", is_flag=True, help="Run functional tests only."
)
@click.option("-v", "verbose", is_flag=True, help="Verbose output.")
def product_test(products, level_unit, level_integration, level_functional, verbose):
    """Run all feature tests for one or more products.

    \b
    Examples:
        splent product:test                              # active product
        splent product:test --product my_app             # explicit product
        splent product:test --product app_a --product app_b --unit
    """
    if not products:
        active = context.active_app()
        if not active:
            click.secho(
                "❌ No product selected and no --product given.\n"
                "   Run: splent product:select <name>  or pass --product <name>",
                fg="red",
            )
            raise SystemExit(1)
        products = (active,)

    workspace = str(context.workspace())

    levels = []
    if level_unit:
        levels.append("--unit")
    if level_integration:
        levels.append("--integration")
    if level_functional:
        levels.append("--functional")

    click.secho(f"\n🧪 product:test — {len(products)} product(s)", bold=True)

    failed = []
    for product in products:
        rc = _run_product_tests(product, workspace, levels, verbose)
        if rc != 0:
            failed.append(product)

    click.echo()
    if not failed:
        click.secho(f"✅ All {len(products)} product(s) passed.", fg="green")
    else:
        click.secho(
            f"❌ {len(failed)}/{len(products)} product(s) failed: {', '.join(failed)}",
            fg="red",
        )
        raise SystemExit(1)


cli_command = product_test
