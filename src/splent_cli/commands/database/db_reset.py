import glob
import importlib
import os
import tomllib

import click
from flask import current_app
from flask_migrate import upgrade as alembic_upgrade
from sqlalchemy import text, MetaData

from splent_cli.utils.decorators import requires_db
from splent_cli.services import context
from splent_cli.utils.lifecycle import advance_state, resolve_feature_key_from_entry
from splent_framework.db import db
from splent_framework.managers.migration_manager import (
    MigrationManager,
    SPLENT_MIGRATIONS_TABLE,
    alembic_version_table,
)
from splent_framework.utils.feature_utils import get_features_from_pyproject
from splent_framework.utils.path_utils import PathUtils
from splent_cli.commands.clear.clear_uploads import clear_uploads


# =====================================================================
# Helpers for the per-feature reset
# =====================================================================
def _normalize(feature_name: str) -> str:
    """Accept 'projects' or 'splent_feature_projects' -> full package name."""
    short = feature_name.split("/")[-1].replace("splent_feature_", "")
    return f"splent_feature_{short}"


def _tables_owned_by(feature_full: str) -> list[str]:
    """Tables whose model classes live in the given feature's package."""
    import_name = f"splent_io.{feature_full}"
    try:
        importlib.import_module(f"{import_name}.models")
    except Exception:
        pass
    tables = []
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if getattr(cls, "__module__", "").startswith(import_name):
            tables.append(cls.__table__.name)
    return sorted(set(tables))


def _refiners_of(feature_full: str, product_path: str) -> list[str]:
    """Installed features whose [tool.splent.refinement].refines == feature."""
    refiners = []
    pattern = os.path.join(product_path, "features", "*", "*", "pyproject.toml")
    for pp in glob.glob(pattern):
        try:
            with open(pp, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            continue
        ref = data.get("tool", {}).get("splent", {}).get("refinement", {})
        if ref.get("refines") == feature_full:
            name = data.get("project", {}).get("name", "")
            if name:
                refiners.append(name)
    return refiners


def _reapply(app, feats: list[str], entry_lookup: dict, product_path, product_name):
    dirs = MigrationManager.get_all_feature_migration_dirs()
    for feat in feats:
        mdir = dirs.get(feat)
        if not mdir:
            continue
        try:
            alembic_upgrade(directory=mdir)
            revision = MigrationManager.get_current_feature_revision(feat, db.engine)
            MigrationManager.update_feature_status(app, feat, revision)
            click.echo(click.style(f"  ✅ {feat} → {revision or 'head'}", fg="green"))
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
                continue
            click.echo(click.style(f"  ❌ {feat}: {e}", fg="red"))
        except Exception as e:
            click.echo(click.style(f"  ❌ {feat}: {e}", fg="red"))


def _entry_lookup() -> dict:
    lookup = {}
    for entry in get_features_from_pyproject() or []:
        key, ns, name, version = resolve_feature_key_from_entry(entry)
        lookup[name] = (key, ns, name, version)
    return lookup


# =====================================================================
# Command
# =====================================================================
@requires_db
@click.command(
    "db:reset",
    short_help="Drop all tables (or just one feature's) and re-apply migrations.",
)
@click.argument("feature_name", required=False)
@click.option("-y", "--yes", is_flag=True, help="Confirm without prompting.")
@context.requires_product
def db_reset(feature_name, yes):
    """Reset the database.

    With no argument: drops ALL tables and re-applies every feature migration.

    With FEATURE_NAME (e.g. ``db:reset projects``): drops only the tables that
    feature owns, clears their migration tracking, and re-applies that
    feature's migrations plus any feature that refines it — leaving every other
    feature's data untouched. Useful after changing a model/refinement.
    """
    app = current_app
    product_path = PathUtils.get_app_base_dir()
    product_name = os.getenv("SPLENT_APP", "")

    if feature_name:
        _reset_one_feature(app, feature_name, yes, product_path, product_name)
        return

    _reset_everything(app, yes, product_path, product_name)


def _reset_one_feature(app, feature_name, yes, product_path, product_name):
    feature_full = _normalize(feature_name)
    tables = _tables_owned_by(feature_full)
    refiners = _refiners_of(feature_full, product_path)
    feats = [feature_full] + refiners

    if not tables:
        click.secho(
            f"⚠️  '{feature_full}' owns no tables (nothing to drop). "
            f"If it is a refinement, reset the base feature instead.",
            fg="yellow",
        )
        return

    click.echo()
    click.secho(f"Reset feature: {feature_full}", fg="cyan", bold=True)
    click.echo(f"  tables to drop : {', '.join(tables)}")
    if refiners:
        click.echo(f"  also re-applies: {', '.join(refiners)} (refiners)")
    if not yes and not click.confirm(
        "⚠️  This drops those tables and re-applies their migrations. Continue?",
        abort=True,
    ):
        return

    # Drop the feature's tables
    with db.engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS `{t}`"))
            click.echo(click.style(f"   Dropped {t}", fg="bright_black"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    # Clear migration tracking for the feature and its refiners — both the
    # central splent_migrations row AND each feature's alembic version table —
    # so their migrations are re-applied from scratch onto the fresh tables.
    with db.engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for feat in feats:
            conn.execute(
                text(f"DELETE FROM `{SPLENT_MIGRATIONS_TABLE}` WHERE feature = :f"),
                {"f": feat},
            )
            conn.execute(text(f"DROP TABLE IF EXISTS `{alembic_version_table(feat)}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    click.secho("⬆️  Re-applying migrations...", fg="cyan")
    _reapply(app, feats, _entry_lookup(), product_path, product_name)
    click.secho(f"\n🎉 Feature '{feature_full}' reset complete.", fg="green")


def _reset_everything(app, yes, product_path, product_name):
    if not yes and not click.confirm(
        "⚠️  WARNING: This will DROP all tables and clear uploads. Are you sure?",
        abort=True,
    ):
        return

    click.echo(click.style("🗑️  Dropping all tables...", fg="yellow"))
    try:
        with db.engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            meta = MetaData()
            meta.reflect(bind=db.engine)
            for table in meta.sorted_tables:
                conn.execute(text(f"DROP TABLE IF EXISTS `{table.name}`"))
                click.echo(click.style(f"   Dropped {table.name}", fg="bright_black"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        click.echo(click.style("✅ All tables dropped.", fg="yellow"))
    except Exception as e:
        click.echo(click.style(f"❌ Error dropping tables: {e}", fg="red"))
        click.echo(
            click.style(
                "⚠️  The database may now be in a PARTIAL state: some tables "
                "may have been dropped while others remain.\n"
                "   To recover, re-run this command once the database is "
                "reachable again (it is safe to repeat — drops use "
                "DROP TABLE IF EXISTS), or manually drop the remaining "
                "tables / re-create the schema from migrations.",
                fg="red",
            )
        )
        raise SystemExit(1)

    click.echo(click.style("📋 Recreating splent_migrations table...", fg="cyan"))
    with db.engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS `{SPLENT_MIGRATIONS_TABLE}` (
                    `feature`        VARCHAR(255) NOT NULL,
                    `last_migration` VARCHAR(255) DEFAULT NULL,
                    PRIMARY KEY (`feature`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
        )

    ctx = click.get_current_context()
    ctx.invoke(clear_uploads)

    dirs = MigrationManager.get_all_feature_migration_dirs()
    if not dirs:
        click.echo(click.style("⚠️  No feature migrations found.", fg="yellow"))
    else:
        click.echo(
            click.style(
                f"⬆️  Applying migrations for {len(dirs)} features...", fg="cyan"
            )
        )
        _reapply(app, list(dirs.keys()), _entry_lookup(), product_path, product_name)

    click.echo(click.style("\n🎉 Database reset complete.", fg="green"))


cli_command = db_reset
