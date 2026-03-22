import os
from splent_cli.services import context
import re
import click
from pathlib import Path
from collections import defaultdict


def _get_feature_usage(workspace: Path) -> dict:
    """Returns {feature_ref: [product_name, ...]} from all products' pyproject.toml."""
    usage = defaultdict(list)
    for product_dir in sorted(workspace.iterdir()):
        if not product_dir.is_dir() or product_dir.name.startswith("."):
            continue
        pyproject = product_dir / "pyproject.toml"
        if not pyproject.exists():
            continue
        content = pyproject.read_text()
        m = re.search(
            r'\[project\.optional-dependencies\].*?features\s*=\s*\[(.*?)\]',
            content,
            re.DOTALL,
        )
        if not m:
            continue
        for raw in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1)):
            ref = raw[0] or raw[1]
            if "/" in ref:
                ref = ref.split("/", 1)[1]
            usage[ref].append(product_dir.name)
    return usage


@click.command("cache:usage", short_help="Show which products use each feature.")
@click.option("--feature", default=None, help="Filter by feature name (partial match).")
def cache_usage(feature):
    """Shows each feature and which product(s) declare it in their pyproject.toml."""
    workspace = context.workspace()

    usage = _get_feature_usage(workspace)
    if not usage:
        click.secho("ℹ️  No features declared in any product.", fg="yellow")
        return

    if feature:
        usage = {k: v for k, v in usage.items() if feature.lower() in k.lower()}
        if not usage:
            click.secho(f"⚠️  No features matching '{feature}'.", fg="yellow")
            return

    click.secho(f"Feature usage across {len(usage)} feature(s):\n", fg="cyan")
    for ref, products in sorted(usage.items()):
        click.secho(f"  {ref}", bold=True)
        for i, p in enumerate(products):
            connector = "└──" if i == len(products) - 1 else "├──"
            click.echo(f"    {connector} {p}")
        click.echo()


cli_command = cache_usage


