import os

import click
from flask_migrate import migrate as alembic_migrate
from flask_migrate import revision as alembic_revision

from splent_cli.utils.decorators import requires_db
from splent_cli.services import context
from splent_cli.utils.lifecycle import (
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
    short_help="Generate a new migration from model changes and apply it.",
)
@click.argument("feature", required=False, default=None)
@click.option(
    "-m", "message", default=None, help="Migration message (defaults to feature name)."
)
@click.option(
    "--empty",
    is_flag=True,
    help=(
        "Generate a blank migration (no autogenerate) and keep it for manual "
        "editing. Required for refinement features that add columns to a base "
        "feature's table — Alembic cannot autodetect runtime-injected mixins."
    ),
)
@context.requires_product
def db_migrate(feature, message, empty):
    """
    Generate new migration files for features that have schema changes,
    then apply all pending migrations.

    Empty migrations (no schema changes detected) are automatically removed,
    unless ``--empty`` is passed, in which case a blank migration is created
    and kept so you can write ``upgrade()``/``downgrade()`` by hand.
    """
    if empty and not feature:
        click.echo(
            click.style("❌ --empty requires a FEATURE argument.", fg="red")
        )
        raise SystemExit(1)

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

        gen_error = None
        try:
            if empty:
                alembic_revision(directory=mdir, message=message or feat)
            else:
                alembic_migrate(directory=mdir, message=message or feat)
        except Exception as e:
            gen_error = e
        finally:
            alembic_logger.setLevel(prev_level)

        # Always surface generation failures — never report "up to date" when the
        # migration actually failed to generate. Show a one-line error (full
        # detail only under SPLENT_DEBUG).
        if gen_error is not None:
            click.echo(
                click.style(
                    f"  ❌ {feat}: migration generation failed ({gen_error})",
                    fg="red",
                )
            )
            if os.getenv("SPLENT_DEBUG"):
                import traceback

                click.echo(
                    "".join(
                        traceback.format_exception(
                            type(gen_error), gen_error, gen_error.__traceback__
                        )
                    )
                )
            continue

        after = _count_versions(mdir)

        # A blank migration requested with --empty is intentional: keep it so the
        # user can fill in upgrade()/downgrade() by hand (e.g. refinement columns).
        if empty:
            if after > before:
                click.echo(
                    click.style(
                        f"  📝 {feat}: blank migration generated — "
                        f"edit upgrade()/downgrade() and run 'splent db:upgrade'",
                        fg="cyan",
                    )
                )
            else:
                click.echo(click.style(f"  ✔ {feat}: up to date", fg="green"))
            continue

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


cli_command = db_migrate
