import os
from splent_cli.services import context, compose
import re
import shutil
import click
from pathlib import Path
from collections import defaultdict


def _get_cache_entries(cache_root: Path) -> list:
    entries = []
    if not cache_root.exists():
        return entries
    for ns_dir in sorted(cache_root.iterdir()):
        if not ns_dir.is_dir():
            continue
        for feat_dir in sorted(ns_dir.iterdir()):
            if not feat_dir.is_dir():
                continue
            feat = feat_dir.name
            if "@" in feat:
                name, version = feat.split("@", 1)
                entries.append({
                    "namespace": ns_dir.name,
                    "name": name,
                    "version": version,
                    "is_versioned": True,
                    "path": feat_dir,
                })
            else:
                entries.append({
                    "namespace": ns_dir.name,
                    "name": feat,
                    "version": None,
                    "is_versioned": False,
                    "path": feat_dir,
                })
    return entries


def _get_all_product_refs(workspace: Path) -> set:
    refs = set()
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
            refs.add(ref)
    return refs


@click.command("cache:prune", short_help="Remove orphaned cache entries not used by any product.")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def cache_prune(yes):
    """
    Removes cache entries that no product references in its pyproject.toml,
    then cleans up broken symlinks in products.
    """
    workspace = context.workspace()
    cache_root = workspace / ".splent_cache" / "features"

    entries = _get_cache_entries(cache_root)
    if not entries:
        click.secho("ℹ️  Feature cache is empty.", fg="yellow")
        return

    refs = _get_all_product_refs(workspace)

    orphans = [
        e for e in entries
        if (f"{e['name']}@{e['version']}" if e["is_versioned"] else e["name"]) not in refs
    ]

    if not orphans:
        click.secho("✅ Nothing to prune — no orphaned entries.", fg="green")
        return

    click.secho(f"Orphaned entries to remove ({len(orphans)}):", fg="yellow")
    for e in orphans:
        label = f"{e['name']}@{e['version']}" if e["is_versioned"] else f"{e['name']}  (editable)"
        click.echo(f"  - {e['namespace']}/{label}")

    click.echo()
    if not yes and not click.confirm("Remove all of the above?"):
        click.echo("❎ Cancelled.")
        raise SystemExit(0)

    for e in orphans:
        shutil.rmtree(e["path"])

    click.secho(f"🧹 Pruned {len(orphans)} orphaned cache entry/entries.", fg="green")

    removed = compose.remove_broken_symlinks(workspace)
    if removed:
        click.secho(f"🔗 Removed {removed} broken feature symlink(s).", fg="yellow")
    else:
        click.secho("✅ No broken symlinks found.", fg="green")


cli_command = cache_prune


