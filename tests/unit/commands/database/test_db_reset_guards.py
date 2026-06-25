"""Tests for db:reset — partial-state / recovery guards on drop & connection
failures, plus the clean success path.

The command (splent_cli.commands.database.db_reset) wipes ALL tables and then
re-applies feature migrations.  The hardened behavior we lock in here:

  * a failure while dropping tables (e.g. a mid-operation connection drop) is
    reported with a clear PARTIAL-state / recovery message and a non-zero exit
    code — never a raw traceback;
  * a failure recreating the tracking table is likewise surfaced cleanly with a
    non-zero exit;
  * the happy path drops every table inside a single transaction and finishes
    with a success message and exit code 0.

Everything below mocks at the boundary: the db engine, current_app, the
migration manager and alembic upgrade.  No real database / docker / network.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from splent_cli.commands.database import db_reset as db_reset_mod
from splent_cli.commands.database.db_reset import db_reset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(begin_side_effect=None, conn=None):
    """Build a fake SQLAlchemy engine whose .begin() is a context manager.

    begin_side_effect: if set, calling engine.begin() raises it (simulates a
        connection failure before any DDL runs).
    conn: the connection object yielded inside the `with` block.
    """
    engine = MagicMock(name="engine")
    if conn is None:
        conn = MagicMock(name="conn")

    @contextmanager
    def _begin():
        if begin_side_effect is not None:
            raise begin_side_effect
        yield conn

    engine.begin.side_effect = lambda: _begin()
    return engine, conn


def _patches(engine):
    """Common patches so the command runs without a real Flask app / db / fs.

    Returns a list of patch objects (use inside an ExitStack-style with).
    """
    db_mock = MagicMock(name="db")
    db_mock.engine = engine
    return db_mock


def _no_traceback(text: str):
    assert "Traceback" not in text
    assert "CalledProcessError" not in text


# ---------------------------------------------------------------------------
# Hardened: drop failures
# ---------------------------------------------------------------------------

class TestDropFailureGuards:
    def test_connection_failure_during_drop_reports_partial_state(self, monkeypatch):
        """engine.begin() raising (connection lost) → clean partial-state msg + exit 1."""
        monkeypatch.setenv("SPLENT_APP", "test_app")

        engine, _ = _make_engine(
            begin_side_effect=OSError("Lost connection to MySQL server during query")
        )
        db_mock = _patches(engine)

        runner = CliRunner(mix_stderr=False)
        with patch.object(db_reset_mod, "db", db_mock), \
             patch.object(db_reset_mod, "current_app", MagicMock()):
            result = runner.invoke(db_reset, ["--yes"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        # Recovery guidance must be present and mention partial state.
        assert "PARTIAL" in combined
        assert "re-run" in combined.lower()
        # No raw traceback leaked to the user.
        _no_traceback(combined)
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_drop_table_execute_failure_reports_partial_state(self, monkeypatch):
        """A DROP failing mid-loop (some tables already gone) → partial-state msg + exit 1."""
        monkeypatch.setenv("SPLENT_APP", "test_app")

        conn = MagicMock(name="conn")
        # SET FOREIGN_KEY_CHECKS ok, then DROP blows up on a connection error.
        conn.execute.side_effect = [
            None,  # SET FOREIGN_KEY_CHECKS = 0
            OSError("Lost connection to MySQL server during query"),  # first DROP
        ]
        engine, _ = _make_engine(conn=conn)
        db_mock = _patches(engine)

        # Reflect one table so the loop has something to drop.
        fake_table = MagicMock()
        fake_table.name = "user"
        fake_meta = MagicMock()
        fake_meta.sorted_tables = [fake_table]

        runner = CliRunner(mix_stderr=False)
        with patch.object(db_reset_mod, "db", db_mock), \
             patch.object(db_reset_mod, "current_app", MagicMock()), \
             patch.object(db_reset_mod, "MetaData", return_value=fake_meta):
            result = runner.invoke(db_reset, ["--yes"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "PARTIAL" in combined
        # Safe-to-repeat guidance must be present.
        assert "DROP TABLE IF EXISTS" in combined
        _no_traceback(combined)

    def test_tracking_table_recreate_failure_reports_clean_error(self, monkeypatch):
        """Drop succeeds but recreating splent_migrations fails → clean msg + exit 1."""
        monkeypatch.setenv("SPLENT_APP", "test_app")

        call_count = {"n": 0}

        @contextmanager
        def _begin():
            call_count["n"] += 1
            if call_count["n"] == 1:
                # STEP 1: drop transaction — everything ok, no tables to drop.
                yield MagicMock(name="drop_conn")
            else:
                # STEP 2: recreate tracking table — connection dies.
                raise OSError("Lost connection to MySQL server during query")

        engine = MagicMock(name="engine")
        engine.begin.side_effect = lambda: _begin()
        db_mock = _patches(engine)

        fake_meta = MagicMock()
        fake_meta.sorted_tables = []  # nothing to drop → STEP 1 succeeds

        runner = CliRunner(mix_stderr=False)
        with patch.object(db_reset_mod, "db", db_mock), \
             patch.object(db_reset_mod, "current_app", MagicMock()), \
             patch.object(db_reset_mod, "MetaData", return_value=fake_meta):
            result = runner.invoke(db_reset, ["--yes"])

        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        # Tracking-table-specific recovery guidance.
        assert "tracking table" in combined.lower()
        assert "re-run" in combined.lower()
        _no_traceback(combined)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestSuccessPath:
    def _run_success(self, monkeypatch, dirs):
        monkeypatch.setenv("SPLENT_APP", "test_app")

        conn = MagicMock(name="conn")
        engine, _ = _make_engine(conn=conn)
        db_mock = _patches(engine)

        fake_t1 = MagicMock()
        fake_t1.name = "user"
        fake_t2 = MagicMock()
        fake_t2.name = "post"
        fake_meta = MagicMock()
        fake_meta.sorted_tables = [fake_t1, fake_t2]

        mm = MagicMock()
        mm.get_all_feature_migration_dirs.return_value = dirs
        mm.get_current_feature_revision.return_value = "abc123"

        runner = CliRunner(mix_stderr=False)
        with patch.object(db_reset_mod, "db", db_mock), \
             patch.object(db_reset_mod, "current_app", MagicMock()), \
             patch.object(db_reset_mod, "MetaData", return_value=fake_meta), \
             patch.object(db_reset_mod, "MigrationManager", mm), \
             patch.object(db_reset_mod, "alembic_upgrade", MagicMock()), \
             patch.object(db_reset_mod, "get_features_from_pyproject", return_value=[]), \
             patch.object(db_reset_mod, "advance_state", MagicMock()), \
             patch.object(db_reset_mod, "PathUtils", MagicMock()), \
             patch.object(db_reset_mod, "clear_uploads", MagicMock()):
            result = runner.invoke(db_reset, ["--yes"])
        return result, conn

    def test_success_no_migrations_drops_cleanly(self, monkeypatch):
        result, conn = self._run_success(monkeypatch, dirs={})

        assert result.exit_code == 0, result.output + (result.stderr or "")
        combined = result.output + (result.stderr or "")
        _no_traceback(combined)
        assert "Database reset complete" in combined
        # Both reflected tables were dropped (the command echoes each drop).
        assert "Dropped user" in combined
        assert "Dropped post" in combined
        # DROP DDL was issued for each reflected table (TextClause.text holds SQL).
        drop_sql = " ".join(
            c.args[0].text for c in conn.execute.call_args_list if c.args
        )
        assert "DROP TABLE IF EXISTS `user`" in drop_sql
        assert "DROP TABLE IF EXISTS `post`" in drop_sql

    def test_success_applies_feature_migrations(self, monkeypatch):
        result, _ = self._run_success(monkeypatch, dirs={"feat_a": "/m/feat_a"})

        assert result.exit_code == 0, result.output + (result.stderr or "")
        combined = result.output + (result.stderr or "")
        _no_traceback(combined)
        assert "Database reset complete" in combined
        assert "feat_a" in combined


# ---------------------------------------------------------------------------
# Confirmation guard
# ---------------------------------------------------------------------------

class TestConfirmationGuard:
    def test_aborts_without_yes_when_declined(self, monkeypatch):
        """Declining the destructive confirmation must NOT drop anything."""
        monkeypatch.setenv("SPLENT_APP", "test_app")

        engine, conn = _make_engine()
        db_mock = _patches(engine)

        runner = CliRunner(mix_stderr=False)
        with patch.object(db_reset_mod, "db", db_mock), \
             patch.object(db_reset_mod, "current_app", MagicMock()):
            result = runner.invoke(db_reset, [], input="n\n")

        assert result.exit_code != 0  # click abort
        engine.begin.assert_not_called()
