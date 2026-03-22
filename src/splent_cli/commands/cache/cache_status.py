import os
from splent_cli.services import context
import click
from pathlib import Path
from collections import defaultdict


def _get_cache_grouped(cache_root: Path) -> dict:
    """Returns {namespace/name: [version_or_None, ...]} from cache."""
    grouped = defaultdict(list)
    if not cache_root.exists():
        return grouped
    for ns_dir in sorted(cache_root.iterdir()):
        if not ns_dir.is_dir():
            continue
        for feat_dir in sorted(ns_dir.iterdir()):
            if not feat_dir.is_dir():
                continue
            feat = feat_dir.name
            if "@" in feat:
                name, version = feat.split("@", 1)
                grouped[f"{ns_dir.name}/{name}"].append(version)
            else:
                grouped[f"{ns_dir.name}/{feat}"].append(None)
    return grouped


@click.command("cache:status", short_help="Show all cached features (versioned vs editable).")
def cache_status():
    """Lists all features in cache, showing which are editable and which are versioned snapshots."""
    workspace = context.workspace()
    cache_root = workspace / ".splent_cache" / "features"

    grouped = _get_cache_grouped(cache_root)
    if not grouped:
        click.secho("ℹ️  Feature cache is empty.", fg="yellow")
        return

    total = sum(len(v) for v in grouped.values())
    click.secho(f"Feature cache — {len(grouped)} feature(s), {total} total entries:\n", fg="cyan")

    for feature, versions in sorted(grouped.items()):
        click.secho(f"  {feature}", bold=True)
        sorted_versions = sorted(versions, key=lambda x: (x is not None, x or ""))
        for i, v in enumerate(sorted_versions):
            connector = "└──" if i == len(sorted_versions) - 1 else "├──"
            if v is None:
                click.echo(f"    {connector} " + click.style("editable", fg="blue"))
            else:
                click.echo(f"    {connector} " + click.style(f"@{v}", fg="green"))
        click.echo()


cli_command = cache_status


