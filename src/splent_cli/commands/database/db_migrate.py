import os

import click
from flask import current_app
from flask_migrate import migrate as alembic_migrate, upgrade as alembic_upgrade

from splent_cli.utils.decorators import requires_db
from splent_cli.utils.lifecycle import (
    advance_state,
    resolve_feature_key_from_entry,
    require_editable,
)
from splent_framework.managers.migration_manager import MigrationManager
from splent_framework.utils.feature_utils import get_features_from_pyproject
from splent_framework.utils.path_utils import PathUtils


def _count_versions(mdir: str) -> int:
    """Count .py migration files in a versions/ directory."""
    versions_dir = os.path.join(mdir, "versions")
    if not os.path.isdir(versions_dir):
        return 0
    return len([f for f in os.listdir(versions_dir) if f.endswith(".py")])


def _is_empty_migration(path: str) -> bool:
    """Check if a migration file only contains pass in upgrade/downgrade."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Strip upgrade() and downgrade() bodies — if both are just pass, it's empty
    import re

    upgrades = re.findall(r"def upgrade\(\).*?(?=\ndef |\Z)", content, re.DOTALL)
    downgrades = re.findall(r"def downgrade\(\).*?(?=\ndef |\Z)", content, re.DOTALL)
    for body in upgrades + downgrades:
        # Remove comments, docstrings, and whitespace — if only 'pass' remains, it's empty
        lines = [
            line.strip()
            for line in body.splitlines()
            if line.strip()
            and not line.strip().startswith("#")
            and not line.strip().startswith("def ")
        ]
        if any(line != "pass" for line in lines):
            return False
    return True


@requires_db
@click.command(
    "db:migrate",
    short_help="Generate and apply migrations (all features or a single one).",
)
@click.argument("feature", required=False, default=None)
def db_migrate(feature):
    """
    Generate new migration files for features that have schema changes,
    then apply all pending migrations.

    Empty migrations (no schema changes detected) are automatically removed.
    """
    app = current_app

    if feature:
        dirs = {}
        mdir = MigrationManager.get_feature_migration_dir(feature)
        if not mdir:
            click.echo(
                click.style(
                    f"❌ No migrations directory found for '{feature}'.", fg="red"
                )
            )
            raise SystemExit(1)
        dirs[feature] = mdir
    else:
        dirs = MigrationManager.get_all_feature_migration_dirs()
        if not dirs:
            click.echo(
                click.style("⚠️  No feature migrations directories found.", fg="yellow")
            )
            return

    # Build entry→key lookup for manifest updates
    product_path = PathUtils.get_app_base_dir()
    product_name = os.getenv("SPLENT_APP", "")
    entry_lookup = {}
    for entry in get_features_from_pyproject() or []:
        key, ns, name, version = resolve_feature_key_from_entry(entry)
        entry_lookup[name] = (key, ns, name, version)

    for feat, mdir in dirs.items():
        # Guard: cannot generate migrations for a pinned (read-only) feature
        info = entry_lookup.get(feat)
        if info:
            key, _, _, _ = info
            try:
                require_editable(product_path, key, command="db:migrate")
            except SystemExit:
                continue

        before = _count_versions(mdir)

        # Suppress Alembic's verbose output during generation
        import logging

        alembic_logger = logging.getLogger("alembic")
        prev_level = alembic_logger.level
        alembic_logger.setLevel(logging.WARNING)

        try:
            alembic_migrate(directory=mdir, message=feat)
        except Exception:
            pass
        finally:
            alembic_logger.setLevel(prev_level)

        after = _count_versions(mdir)

        # If a new file was generated, check if it's empty (no real schema changes)
        if after > before:
            versions_dir = os.path.join(mdir, "versions")
            newest = max(
                (
                    os.path.join(versions_dir, f)
                    for f in os.listdir(versions_dir)
                    if f.endswith(".py")
                ),
                key=os.path.getmtime,
            )
            if _is_empty_migration(newest):
                os.remove(newest)
                click.echo(click.style(f"  ✔ {feat}: up to date", fg="green"))
            else:
                click.echo(
                    click.style(f"  📝 {feat}: new migration generated", fg="cyan")
                )
        else:
            click.echo(click.style(f"  ✔ {feat}: up to date", fg="green"))

        # Apply pending migrations
        try:
            alembic_upgrade(directory=mdir)
            revision = MigrationManager.get_current_feature_revision(
                feat, app.extensions["migrate"].db.engine
            )
            MigrationManager.update_feature_status(app, feat, revision)
            click.echo(click.style(f"  ✅ {feat} → {revision or 'head'}", fg="green"))

            # Advance lifecycle state to "migrated"
            info = entry_lookup.get(feat)
            if info:
                key, ns, name, version = info
                advance_state(
                    product_path,
                    product_name,
                    key,
                    to="migrated",
                    namespace=ns,
                    name=name,
                    version=version,
                )
        except Exception as e:
            click.echo(click.style(f"  ❌ {feat}: {e}", fg="red"))


cli_command = db_migrate
