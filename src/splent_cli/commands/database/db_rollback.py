import os

import click
from flask import current_app
from flask_migrate import downgrade as alembic_downgrade

from splent_cli.utils.decorators import requires_db
from splent_cli.utils.lifecycle import advance_state, resolve_feature_key_from_entry
from splent_framework.managers.migration_manager import MigrationManager
from splent_framework.utils.feature_utils import get_features_from_pyproject
from splent_framework.utils.path_utils import PathUtils


@requires_db
@click.command(
    "db:rollback",
    short_help="Roll back migrations for a feature.",
)
@click.argument("feature")
@click.option(
    "--steps", default=1, show_default=True, help="Number of migrations to roll back."
)
def db_rollback(feature, steps):
    app = current_app

    mdir = MigrationManager.get_feature_migration_dir(feature)
    if not mdir:
        click.echo(
            click.style(f"❌ No migrations directory found for '{feature}'.", fg="red")
        )
        raise SystemExit(1)

    click.echo(
        click.style(f"  ⬇️  Rolling back {steps} step(s) for {feature}...", fg="cyan")
    )
    try:
        alembic_downgrade(directory=mdir, revision=f"-{steps}")
        revision = MigrationManager.get_current_feature_revision(
            feature, app.extensions["migrate"].db.engine
        )
        MigrationManager.update_feature_status(app, feature, revision)
        click.echo(click.style(f"  ✅ {feature} → {revision or 'base'}", fg="green"))

        # Regress lifecycle state: if fully rolled back → "installed", else stays "migrated"
        product_path = PathUtils.get_app_base_dir()
        product_name = os.getenv("SPLENT_APP", "")
        for entry in get_features_from_pyproject() or []:
            key, ns, name, version = resolve_feature_key_from_entry(entry)
            if name == feature:
                target = "installed" if revision is None else "migrated"
                advance_state(
                    product_path,
                    product_name,
                    key,
                    to=target,
                    namespace=ns,
                    name=name,
                    version=version,
                )
                break
    except Exception as e:
        click.echo(click.style(f"  ❌ {feature}: {e}", fg="red"))


cli_command = db_rollback
