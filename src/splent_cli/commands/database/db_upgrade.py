import contextlib
import logging
import os
import re

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
    product = os.getenv("SPLENT_APP", "")
    product_path = PathUtils.get_app_base_dir()
    return product_path, product


# Alembic revision ids as declared at the top of every migration script.
_REVISION_RE = re.compile(r"^revision(?::\s*str)?\s*=\s*['\"]([^'\"]+)['\"]", re.M)

# The error alembic raises when the database points at a script it cannot find.
_MISSING_REVISION_RE = re.compile(r"[Cc]an't locate revision identified by '([^']+)'")


class _ErrorCollector(logging.Handler):
    """Keep the ERROR records alembic and flask_migrate emit while upgrading."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@contextlib.contextmanager
def _collect_alembic_errors():
    """Yield a list that collects the errors logged during an upgrade.

    flask_migrate logs alembic's CommandError and then calls sys.exit, so the
    message only ever exists as a log record.  Collecting it is the only way to
    tell the user what alembic actually complained about.
    """
    collector = _ErrorCollector()
    loggers = [logging.getLogger("alembic"), logging.getLogger("flask_migrate")]
    for target in loggers:
        target.addHandler(collector)
    try:
        yield collector.messages
    finally:
        for target in loggers:
            target.removeHandler(collector)


def _script_revisions(migrations_dir: str) -> set[str]:
    """Return every revision id shipped under <migrations_dir>/versions."""
    versions_dir = os.path.join(migrations_dir, "versions")
    revisions: set[str] = set()
    if not os.path.isdir(versions_dir):
        return revisions
    for fname in sorted(os.listdir(versions_dir)):
        if not fname.endswith(".py"):
            continue
        try:
            with open(os.path.join(versions_dir, fname), encoding="utf-8") as f:
                revisions.update(_REVISION_RE.findall(f.read()))
        except OSError:
            continue
    return revisions


def _db_revision(app, feature: str) -> str | None:
    """Return the revision recorded in alembic_<feature>, or None if unreadable."""
    try:
        engine = app.extensions["migrate"].db.engine
        return MigrationManager.get_current_feature_revision(feature, engine)
    except Exception:
        # No app context, no DB, no migrate extension — diagnosis is best effort.
        return None


def _out_of_sync_message(feature: str, db_revision: str, known: set[str]) -> str:
    """Explain that the database is ahead of the pinned code, and how to get out."""
    shipped = ", ".join(sorted(known)) if known else "no revisions at all"
    return (
        f"  ❌ {feature} is out of sync with the database.\n"
        f"     The database is at revision '{db_revision}' and the installed code "
        f"does not contain the script that created it.\n"
        f"     The pinned version of {feature} ships {shipped}.\n"
        f"     Ways out\n"
        f"       - splent feature:unlock {feature}, to go back to the editable "
        f"version that produced that revision\n"
        f"       - splent db:rollback {feature} --steps 999, to undo its "
        f"migrations while the code that created them is still installed\n"
        f"       - splent db:reset, to wipe the development database and migrate "
        f"again from scratch"
    )


def _diagnose_upgrade_failure(
    app, feature: str, migrations_dir: str, error=None
) -> str:
    """Turn a failed (or silently exited) alembic upgrade into a readable error.

    The common cause after pinning a feature is a database that is ahead of the
    code: alembic cannot find the revision the DB points at, flask_migrate logs
    the CommandError and calls sys.exit, and the user sees nothing at all.
    """
    detail = str(error) if error is not None else ""

    # Prefer the revision alembic itself complained about, fall back to the DB.
    match = _MISSING_REVISION_RE.search(detail)
    db_revision = match.group(1) if match else _db_revision(app, feature)
    known = _script_revisions(migrations_dir)

    if db_revision and db_revision not in known:
        return _out_of_sync_message(feature, db_revision, known)

    if not detail:
        detail = "alembic exited without reporting an error."
    return f"  ❌ {feature}: {detail}"


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
    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)

    failures = []
    for feat, mdir in dirs.items():
        try:
            logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)
            with _collect_alembic_errors() as logged:
                try:
                    alembic_upgrade(directory=mdir)
                except SystemExit:
                    # flask_migrate swallows alembic's CommandError and calls
                    # sys.exit(1), so catching the exit is the only way to say
                    # anything at all instead of failing in silence.
                    click.echo(
                        click.style(
                            _diagnose_upgrade_failure(
                                app, feat, mdir, "; ".join(logged) or None
                            ),
                            fg="red",
                        )
                    )
                    failures.append(feat)
                    continue
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
            failures.append(feat)
        except Exception as e:
            click.echo(
                click.style(_diagnose_upgrade_failure(app, feat, mdir, e), fg="red")
            )
            failures.append(feat)

    if failures:
        raise click.ClickException(
            "Migration upgrade failed for: " + ", ".join(failures)
        )


cli_command = db_upgrade
