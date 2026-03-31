import os
import re

import click
from flask import current_app

from splent_cli.utils.decorators import requires_db
from splent_cli.services import context
from splent_framework.managers.migration_manager import MigrationManager


def _get_filesystem_head(mdir: str) -> str | None:
    """Walk the migration chain on disk and return the head revision."""
    versions_dir = os.path.join(mdir, "versions")
    if not os.path.isdir(versions_dir):
        return None

    # Parse all migration files: revision + down_revision
    revisions: dict[str, str | None] = {}  # {revision: down_revision}
    for f in os.listdir(versions_dir):
        if not f.endswith(".py"):
            continue
        path = os.path.join(versions_dir, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            rev_m = re.search(r"^revision\s*=\s*['\"](\w+)['\"]", content, re.MULTILINE)
            down_m = re.search(
                r"^down_revision\s*=\s*['\"](\w+)['\"]", content, re.MULTILINE
            )
            if rev_m:
                revisions[rev_m.group(1)] = down_m.group(1) if down_m else None
        except Exception:
            continue

    if not revisions:
        return None

    # Head = revision that is not a down_revision of any other
    all_down = set(v for v in revisions.values() if v)
    heads = [r for r in revisions if r not in all_down]
    return heads[0] if heads else None


@requires_db
@click.command(
    "db:status",
    short_help="Show migration status for all features.",
)
@context.requires_product
def db_status():
    """Show migration status: DB revision vs filesystem head for each feature."""
    app = current_app

    click.echo(click.style("\n📊 Migration Status\n", fg="cyan", bold=True))

    try:
        rows = MigrationManager.get_all_status(app)
    except Exception as e:
        click.echo(click.style(f"❌ Could not read splent_migrations: {e}", fg="red"))
        raise SystemExit(1)

    # Get all feature migration dirs for filesystem cross-check
    dirs = MigrationManager.get_all_feature_migration_dirs()

    # Declared feature names (from pyproject.toml) — used to detect orphans
    declared_features = set(dirs.keys())
    try:
        from splent_framework.utils.pyproject_reader import PyprojectReader

        product_dir = os.path.join(str(context.workspace()), context.require_app())
        reader = PyprojectReader.for_product(product_dir)
        env = os.getenv("SPLENT_ENV")
        for entry in reader.features_for_env(env):
            name = entry.split("@")[0]
            if "/" in name:
                name = name.split("/")[-1]
            declared_features.add(name)
    except Exception:
        pass

    # Merge: features from DB + features from filesystem
    all_features = set(feat for feat, _ in rows) | set(dirs.keys())

    if not all_features:
        click.echo(click.style("  (no features tracked yet)", fg="yellow"))
        return

    db_revisions = {feat: rev for feat, rev in rows}

    col_feat = max(len(f) for f in all_features)
    col_feat = max(col_feat, len("Feature"))
    col_rev = 14

    click.echo(
        f"  {'Feature':<{col_feat}}  {'Applied':<{col_rev}}  {'Latest':<{col_rev}}  Status"
    )
    click.echo(f"  {'-' * col_feat}  {'-' * col_rev}  {'-' * col_rev}  {'-' * 12}")

    issues = 0
    orphans = []
    for feat in sorted(all_features):
        db_rev = db_revisions.get(feat)
        mdir = dirs.get(feat)
        fs_head = _get_filesystem_head(mdir) if mdir else None

        db_display = db_rev[:12] if db_rev else "—"
        fs_display = fs_head[:12] if fs_head else "—"

        if db_rev and fs_head and db_rev == fs_head:
            status = click.style("✔ synced", fg="green")
        elif db_rev and fs_head and db_rev != fs_head:
            status = click.style("⚠ pending", fg="yellow")
            issues += 1
        elif db_rev and not fs_head and feat not in declared_features:
            # Feature has a DB entry but is no longer declared — orphan
            status = click.style("⚠ orphan", fg="red")
            orphans.append(feat)
        elif db_rev and not fs_head:
            status = click.style("⚠ no files", fg="yellow")
            issues += 1
        elif not db_rev and fs_head:
            status = click.style("⚠ not applied", fg="yellow")
            issues += 1
        else:
            status = click.style("— none", fg="bright_black")

        click.echo(
            f"  {feat:<{col_feat}}  {db_display:<{col_rev}}  {fs_display:<{col_rev}}  {status}"
        )

    click.echo()
    if issues:
        click.secho(
            f"  {issues} feature(s) out of sync. Run 'splent db:upgrade' to apply pending migrations.",
            fg="yellow",
        )
        click.echo()
    if orphans:
        click.secho(
            f"  {len(orphans)} orphan(s) in DB no longer declared: {', '.join(orphans)}",
            fg="red",
        )
        click.secho(
            "  Run 'splent db:rollback <feature>' to clean up, or ignore if tables should be kept.",
            fg="red",
        )
        click.echo()


cli_command = db_status
