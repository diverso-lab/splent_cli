import click
from flask import current_app
from flask_migrate import upgrade as alembic_upgrade

from splent_cli.utils.decorators import requires_db
from splent_cli.services import context
from splent_cli.utils.lifecycle import advance_state, resolve_feature_key_from_entry
from splent_framework.managers.migration_manager import MigrationManager
from splent_framework.utils.feature_utils import get_features_from_pyproject
from splent_framework.utils.path_utils import PathUtils


def _resolve_product():
    """Return (product_path, product_name) from env."""
    import os

    product = os.getenv("SPLENT_APP", "")
    product_path = PathUtils.get_app_base_dir()
    return product_path, product


@requires_db
@click.command(
    "db:upgrade",
    short_help="Apply pending migrations (all features or a single one).",
)
@click.argument("feature", required=False, default=None)
@context.requires_product
def db_upgrade(feature):
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
    product_path, product_name = _resolve_product()
    entry_lookup = {}
    for entry in get_features_from_pyproject() or []:
        key, ns, name, version = resolve_feature_key_from_entry(entry)
        entry_lookup[name] = (key, ns, name, version)

    # Suppress Alembic's verbose INFO output
    import logging

    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)

    for feat, mdir in dirs.items():
        try:
            logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)
            alembic_upgrade(directory=mdir)
            revision = MigrationManager.get_current_feature_revision(
                feat, app.extensions["migrate"].db.engine
            )
            MigrationManager.update_feature_status(app, feat, revision)
            click.echo(click.style(f"    {feat} -> {revision or 'head'}", fg="green"))

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
        except ImportError as e:
            if "models" in str(e):
                # Feature has migrations/ dir but no models module — skip silently
                continue
            click.echo(click.style(f"  ❌ {feat}: {e}", fg="red"))
        except Exception as e:
            click.echo(click.style(f"  ❌ {feat}: {e}", fg="red"))


cli_command = db_upgrade
