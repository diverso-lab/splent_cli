import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    load_product_features,
    parse_feature_entry,
)


def _load_feature_map(product_name: str) -> dict[tuple[str, str], str]:
    """Return {(org, package): "org/package"} for a product in the workspace.

    Features are keyed by (organisation, package) so that two features only
    match when BOTH the org/user and the package coincide. The version is
    ignored on purpose — a product on v1.2.0 and another on v1.3.0 of the same
    feature still share that feature.
    """
    product_dir = context.workspace() / product_name
    if not (product_dir / "pyproject.toml").is_file():
        raise click.ClickException(
            f"Product not found: '{product_name}' (no pyproject.toml in {product_dir})"
        )

    feature_map: dict[tuple[str, str], str] = {}
    for entry in load_product_features(str(product_dir)):
        org, package, _version = parse_feature_entry(entry)
        feature_map[(org, package)] = f"{org}/{package}"
    return feature_map


def _print_block(title: str, color: str, keys: list[tuple[str, str]]) -> None:
    click.secho(f"  {title} ({len(keys)})", fg=color, bold=True)
    if not keys:
        click.secho("    —", fg="bright_black")
    for org, package in keys:
        click.echo(f"    {org}/{package}")
    click.echo()


@click.command(
    "spl:compare",
    short_help="Compare the features of two products (shared vs product-specific)",
)
@click.argument("product_a")
@click.argument("product_b")
@context.requires_detached
def spl_compare(product_a, product_b):
    """Compare two products in the workspace by their feature sets.

    Reports the features they SHARE and the features SPECIFIC to each one.
    Matching is done by organisation/package — two features are "the same"
    only when both the org (or user) and the package name coincide.

    \b
    Example:
        splent spl:compare innosoft_app diversolab_app
    """
    a = _load_feature_map(product_a)
    b = _load_feature_map(product_b)

    a_keys, b_keys = set(a), set(b)
    shared = sorted(a_keys & b_keys)
    only_a = sorted(a_keys - b_keys)
    only_b = sorted(b_keys - a_keys)

    union = a_keys | b_keys
    reuse_pct = (len(shared) / len(union) * 100) if union else 0.0

    click.echo()
    click.secho(f"  {product_a}  ↔  {product_b}", fg="cyan", bold=True)
    click.echo(
        f"  {len(a_keys)} feature(s) in {product_a}"
        f"   ·   {len(b_keys)} feature(s) in {product_b}"
    )
    reuse_color = "green" if reuse_pct >= 50 else "yellow"
    click.secho(
        f"  Reuse: {len(shared)}/{len(union)} shared ({reuse_pct:.0f}%)",
        fg=reuse_color,
        bold=True,
    )
    click.echo()

    _print_block("Shared", "green", shared)
    _print_block(f"Only in {product_a}", "yellow", only_a)
    _print_block(f"Only in {product_b}", "magenta", only_b)


cli_command = spl_compare
