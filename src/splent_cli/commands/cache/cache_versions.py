import os
from splent_cli.services import context
import click
from pathlib import Path


@click.command("cache:versions", short_help="List all cached versions of a feature.")
@click.argument("feature_ref")
def cache_versions(feature_ref: str):
    """
    Show all versions of FEATURE_REF available in the local cache.

    \b
    FEATURE_REF format: namespace/feature_name
    Example:            splent_io/auth
    """
    if "/" not in feature_ref:
        click.secho("❌ Use format: namespace/feature_name", fg="red")
        raise SystemExit(1)

    ns, name = feature_ref.split("/", 1)
    ns_fs = ns.replace("-", "_")

    workspace = context.workspace()
    ns_dir = workspace / ".splent_cache" / "features" / ns_fs

    if not ns_dir.exists():
        click.secho(f"⚠️  Namespace '{ns_fs}' not found in cache.", fg="yellow")
        return

    editable = ns_dir / name
    versioned = sorted(
        d for d in ns_dir.iterdir()
        if d.is_dir() and d.name.startswith(f"{name}@")
    )

    if not editable.exists() and not versioned:
        click.secho(f"⚠️  No cache entries found for {ns_fs}/{name}.", fg="yellow")
        return

    click.secho(f"Cache entries for {ns_fs}/{name}:", fg="cyan")
    if editable.exists() and editable.is_dir():
        click.echo("  " + click.style("editable", fg="blue") + "  (no version)")
    for v_dir in versioned:
        version = v_dir.name.split("@", 1)[1]
        click.echo("  " + click.style(f"@{version}", fg="green"))


cli_command = cache_versions


