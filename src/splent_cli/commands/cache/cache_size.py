import os
from splent_cli.services import context
import click
from pathlib import Path


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _human(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


@click.command("cache:size", short_help="Show disk usage of the feature cache.")
def cache_size():
    """Shows disk usage per namespace and feature entry in the cache."""
    workspace = context.workspace()
    cache_root = workspace / ".splent_cache" / "features"

    if not cache_root.exists():
        click.secho("ℹ️  Feature cache is empty.", fg="yellow")
        return

    total = 0
    namespaces = sorted(d for d in cache_root.iterdir() if d.is_dir())

    if not namespaces:
        click.secho("ℹ️  Feature cache is empty.", fg="yellow")
        return

    for ns_dir in namespaces:
        ns_size = _dir_size(ns_dir)
        total += ns_size
        click.secho(f"  {ns_dir.name}  {_human(ns_size)}", bold=True)

        entries = sorted(d for d in ns_dir.iterdir() if d.is_dir())
        for i, feat_dir in enumerate(entries):
            size = _dir_size(feat_dir)
            connector = "└──" if i == len(entries) - 1 else "├──"
            name = feat_dir.name
            label = click.style(f"@{name.split('@')[1]}", fg="green") if "@" in name else click.style("editable", fg="blue")
            base = name.split("@")[0] if "@" in name else name
            click.echo(f"    {connector} {base}  {label}  {_human(size)}")
        click.echo()

    click.secho(f"  Total: {_human(total)}", fg="cyan", bold=True)


cli_command = cache_size


