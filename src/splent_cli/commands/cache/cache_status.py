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


def _get_workspace_root_features(workspace: Path) -> dict:
    """Returns {namespace/name: ['workspace']} for editable features at workspace root."""
    grouped = defaultdict(list)
    for entry in sorted(workspace.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("splent_feature_"):
            continue
        # Must have pyproject.toml to be a real feature
        if not (entry / "pyproject.toml").exists():
            continue
        src = entry / "src"
        if not src.is_dir():
            continue
        for ns_dir in src.iterdir():
            if (ns_dir.is_dir()
                    and not ns_dir.name.startswith(("_", "."))
                    and "." not in ns_dir.name
                    and (ns_dir / entry.name).is_dir()):
                grouped[f"{ns_dir.name}/{entry.name}"].append("workspace")
                break
    return grouped


@click.command(
    "cache:status", short_help="Show all cached features (versioned vs editable)."
)
def cache_status():
    """Lists all features in cache and workspace root, showing which are editable and which are versioned snapshots."""
    workspace = context.workspace()
    cache_root = workspace / ".splent_cache" / "features"

    grouped = _get_cache_grouped(cache_root)
    # Merge editable features from workspace root
    for key, vals in _get_workspace_root_features(workspace).items():
        grouped[key].extend(vals)

    if not grouped:
        click.secho("ℹ️  Feature cache is empty.", fg="yellow")
        return

    total = sum(len(v) for v in grouped.values())
    click.secho(
        f"Feature cache — {len(grouped)} feature(s), {total} total entries:\n",
        fg="cyan",
    )

    for feature, versions in sorted(grouped.items()):
        click.secho(f"  {feature}", bold=True)
        sorted_versions = sorted(versions, key=lambda x: (x is not None, x or ""))
        for i, v in enumerate(sorted_versions):
            connector = "└──" if i == len(sorted_versions) - 1 else "├──"
            if v == "workspace":
                click.echo(f"    {connector} " + click.style("editable (workspace root)", fg="magenta"))
            elif v is None:
                click.echo(f"    {connector} " + click.style("editable (cache)", fg="blue"))
            else:
                click.echo(f"    {connector} " + click.style(f"@{v}", fg="green") + click.style(" (pinned)", fg="bright_black"))
        click.echo()


cli_command = cache_status
