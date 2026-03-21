import os
import re
import click
from pathlib import Path



def _get_cache_entries(cache_root: Path) -> list:
    """Returns list of {namespace, name, version, is_versioned} dicts."""
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
                })
            else:
                entries.append({
                    "namespace": ns_dir.name,
                    "name": feat,
                    "version": None,
                    "is_versioned": False,
                })
    return entries


def _get_all_product_refs(workspace: Path) -> set:
    """Returns set of 'name' and 'name@version' (no namespace) from all products' pyproject.toml."""
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
            # Strip namespace if present (e.g. "splent_io/auth@v1.0" → "auth@v1.0")
            if "/" in ref:
                ref = ref.split("/", 1)[1]
            refs.add(ref)
    return refs


@click.command("cache:orphans", short_help="Show cached features not referenced by any product.")
def cache_orphans():
    """Lists features in the cache that no product references in its pyproject.toml."""
    workspace = Path(os.getenv("WORKING_DIR", "/workspace"))
    cache_root = workspace / ".splent_cache" / "features"

    entries = _get_cache_entries(cache_root)
    if not entries:
        click.secho("ℹ️  Feature cache is empty.", fg="yellow")
        return

    refs = _get_all_product_refs(workspace)

    orphans = []
    for e in entries:
        # Match against just name[@version] — pyproject doesn't include namespace
        ref_key = f"{e['name']}@{e['version']}" if e["is_versioned"] else e["name"]
        if ref_key not in refs:
            display_key = f"{e['namespace']}/{ref_key}"
            orphans.append(display_key)

    if not orphans:
        click.secho("✅ No orphaned features in cache.", fg="green")
        return

    click.secho(f"🗑  Orphaned cache entries ({len(orphans)}):", fg="yellow")
    for o in orphans:
        click.echo(f"  - {o}")


cli_command = cache_orphans
