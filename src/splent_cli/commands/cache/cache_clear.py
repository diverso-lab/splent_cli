import os
import shutil
import click
from pathlib import Path


def _remove_broken_symlinks(workspace: Path) -> int:
    removed = 0
    for product_dir in workspace.iterdir():
        features_dir = product_dir / "features"
        if not features_dir.is_dir():
            continue
        for org_dir in features_dir.iterdir():
            if not org_dir.is_dir():
                continue
            for link in org_dir.iterdir():
                if link.is_symlink() and not link.exists():
                    link.unlink()
                    removed += 1
    return removed


@click.command("cache:clear", short_help="Clear the feature cache (total or partial).")
@click.option("--namespace", default=None, help="Clear only a specific namespace (e.g. splent_io).")
@click.option("--feature", default=None, help="Clear all entries for a specific feature (e.g. splent_feature_auth).")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def cache_clear(namespace, feature, yes):
    """
    Deletes entries from the feature cache and removes broken symlinks in products.

    \b
    Scope (most to least specific):
      --feature splent_feature_auth   → removes all cache entries for that feature
      --namespace splent_io           → removes the entire namespace folder
      (no options)                    → clears the entire feature cache
    """
    workspace = Path(os.getenv("WORKING_DIR", "/workspace"))
    cache_root = workspace / ".splent_cache" / "features"

    if not cache_root.exists():
        click.secho("⚠️  No .splent_cache/features directory found.", fg="yellow")
        raise SystemExit(0)

    # Resolve targets
    if feature:
        ns_filter = namespace or "*"
        targets = []
        search_in = [cache_root / namespace] if namespace else cache_root.iterdir()
        for ns_dir in (search_in if namespace else cache_root.iterdir()):
            ns_dir = Path(ns_dir)
            if not ns_dir.is_dir():
                continue
            for feat_dir in ns_dir.iterdir():
                base = feat_dir.name.split("@")[0]
                if base == feature:
                    targets.append(feat_dir)
        if not targets:
            label = f"'{feature}'" + (f" in namespace '{namespace}'" if namespace else "")
            click.secho(f"⚠️  No cache entries found for {label}.", fg="yellow")
            raise SystemExit(0)
        description = f"{len(targets)} entry/entries for feature '{feature}'"
    elif namespace:
        targets = [cache_root / namespace]
        if not targets[0].exists():
            click.secho(f"⚠️  Namespace '{namespace}' not found in cache.", fg="yellow")
            raise SystemExit(0)
        description = f"namespace '{namespace}'"
    else:
        targets = [cache_root]
        description = "entire feature cache"

    if not yes and not click.confirm(f"⚠️  This will permanently delete the {description}. Continue?"):
        click.echo("❎ Cancelled.")
        raise SystemExit(0)

    # Delete
    for t in targets:
        shutil.rmtree(t)
    cache_root.mkdir(parents=True, exist_ok=True)
    click.secho(f"🧹 Cleared: {description}.", fg="green")

    # Clean broken symlinks
    removed = _remove_broken_symlinks(workspace)
    if removed:
        click.secho(f"🔗 Removed {removed} broken feature symlink(s).", fg="yellow")
    else:
        click.secho("✅ No broken symlinks found.", fg="green")


cli_command = cache_clear
