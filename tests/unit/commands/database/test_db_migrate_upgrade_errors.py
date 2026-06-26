"""Tests for db:migrate and db:upgrade error surfacing.

Hardened behaviors under test:
  * Alembic generation/upgrade FAILURES are surfaced to the user even when
    SPLENT_DEBUG is NOT set — the user must see the error, never a false
    "up to date"/success.
  * For db:migrate, "no changes" (empty migration auto-removed) is clearly
    distinguished from "generation failed".

All alembic / DB / filesystem-dependency boundaries are mocked; no real
docker / git / network / database is required.
"""

import os
from unittest.mock import patch, MagicMock

from click.testing import CliRunner

from splent_cli.commands.database.db_migrate import db_migrate
from splent_cli.commands.database.db_upgrade import db_upgrade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_versions_dir(tmp_path, feature="auth"):
    """Create a feature migrations dir with an empty versions/ subdir."""
    mdir = tmp_path / "features" / feature / "migrations"
    (mdir / "versions").mkdir(parents=True)
    return str(mdir)


def _write_migration(versions_dir, name, empty=True):
    """Write a fake alembic migration file into versions/."""
    if empty:
        body_up = "    pass\n"
        body_down = "    pass\n"
    else:
        body_up = "    op.add_column('t', sa.Column('c', sa.Integer()))\n"
        body_down = "    op.drop_column('t', 'c')\n"
    content = "def upgrade():\n" + body_up + "\n\ndef downgrade():\n" + body_down
    path = os.path.join(versions_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# db:migrate — generation failures are surfaced
# ---------------------------------------------------------------------------


class TestDbMigrateGenerationFailure:
    def test_generation_failure_surfaced_without_debug(self, tmp_path, monkeypatch):
        """A failing alembic_migrate must be reported, NOT swallowed into
        a false 'up to date', even when SPLENT_DEBUG is unset."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.delenv("SPLENT_DEBUG", raising=False)

        mdir = _make_versions_dir(tmp_path, "auth")

        with (
            patch(
                "splent_cli.commands.database.db_migrate."
                "MigrationManager.get_feature_migration_dir",
                return_value=mdir,
            ),
            patch(
                "splent_cli.commands.database.db_migrate.get_features_from_pyproject",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.database.db_migrate.alembic_migrate",
                side_effect=RuntimeError("alembic autogenerate exploded"),
            ),
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_migrate, ["auth"])

        # The error must reach the user.
        assert "❌" in result.output
        assert "failed" in result.output.lower()
        assert "auth" in result.output
        # It must NOT falsely claim success ('✔ ... up to date').
        assert "✔" not in result.output
        assert "up to date" not in result.output
        # Without SPLENT_DEBUG, no raw traceback is dumped.
        assert "Traceback" not in result.output

    def test_generation_failure_full_traceback_only_under_debug(
        self, tmp_path, monkeypatch
    ):
        """With SPLENT_DEBUG set, the full traceback is additionally shown,
        but the one-line error is always present either way."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.setenv("SPLENT_DEBUG", "1")

        mdir = _make_versions_dir(tmp_path, "auth")

        with (
            patch(
                "splent_cli.commands.database.db_migrate."
                "MigrationManager.get_feature_migration_dir",
                return_value=mdir,
            ),
            patch(
                "splent_cli.commands.database.db_migrate.get_features_from_pyproject",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.database.db_migrate.alembic_migrate",
                side_effect=RuntimeError("boom-detail"),
            ),
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_migrate, ["auth"])

        assert "failed" in result.output.lower()
        assert "Traceback" in result.output
        assert "boom-detail" in result.output

    def test_no_changes_distinguished_from_failure(self, tmp_path, monkeypatch):
        """When generation succeeds but produces an EMPTY migration, the file
        is removed and the feature is reported 'up to date' — this must be
        clearly different from a generation failure."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.delenv("SPLENT_DEBUG", raising=False)

        mdir = _make_versions_dir(tmp_path, "auth")
        versions_dir = os.path.join(mdir, "versions")

        def fake_migrate(directory=None, message=None):
            # Simulate alembic writing an empty (pass/pass) migration file.
            _write_migration(versions_dir, "0001_auth.py", empty=True)

        with (
            patch(
                "splent_cli.commands.database.db_migrate."
                "MigrationManager.get_feature_migration_dir",
                return_value=mdir,
            ),
            patch(
                "splent_cli.commands.database.db_migrate.get_features_from_pyproject",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.database.db_migrate.alembic_migrate",
                side_effect=fake_migrate,
            ),
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_migrate, ["auth"])

        assert result.exit_code == 0
        assert "up to date" in result.output
        assert "failed" not in result.output.lower()
        # The empty migration must have been removed.
        assert not os.path.exists(os.path.join(versions_dir, "0001_auth.py"))

    def test_real_changes_reported_as_new_migration(self, tmp_path, monkeypatch):
        """A non-empty generated migration is kept and reported as new."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.delenv("SPLENT_DEBUG", raising=False)

        mdir = _make_versions_dir(tmp_path, "auth")
        versions_dir = os.path.join(mdir, "versions")

        def fake_migrate(directory=None, message=None):
            _write_migration(versions_dir, "0001_auth.py", empty=False)

        with (
            patch(
                "splent_cli.commands.database.db_migrate."
                "MigrationManager.get_feature_migration_dir",
                return_value=mdir,
            ),
            patch(
                "splent_cli.commands.database.db_migrate.get_features_from_pyproject",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.database.db_migrate.alembic_migrate",
                side_effect=fake_migrate,
            ),
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_migrate, ["auth"])

        assert result.exit_code == 0
        assert "new migration generated" in result.output
        assert "up to date" not in result.output
        # The non-empty migration must have been kept.
        assert os.path.exists(os.path.join(versions_dir, "0001_auth.py"))

    def test_missing_feature_dir_clean_error(self, tmp_path, monkeypatch):
        """An unknown feature produces a clean error, not a traceback."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        with patch(
            "splent_cli.commands.database.db_migrate."
            "MigrationManager.get_feature_migration_dir",
            return_value=None,
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_migrate, ["ghost"])

        assert result.exit_code == 1
        assert "No migrations directory" in result.output
        assert "Traceback" not in result.output

    def test_no_product_selected_aborts(self, tmp_path, monkeypatch):
        """db:migrate requires a selected product (SPLENT_APP)."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("SPLENT_APP", raising=False)

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(db_migrate, [])
        assert result.exit_code == 1
        assert "No product selected" in result.output


# ---------------------------------------------------------------------------
# db:upgrade — upgrade failures are surfaced
# ---------------------------------------------------------------------------


class TestDbUpgradeFailure:
    def test_upgrade_failure_surfaced_without_debug(self, tmp_path, monkeypatch):
        """A failing alembic_upgrade must produce a non-zero exit and a clean
        error message — never a silent/false success."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.delenv("SPLENT_DEBUG", raising=False)

        mdir = _make_versions_dir(tmp_path, "auth")

        with (
            patch(
                "splent_cli.commands.database.db_upgrade."
                "MigrationManager.get_feature_migration_dir",
                return_value=mdir,
            ),
            patch(
                "splent_cli.commands.database.db_upgrade.get_features_from_pyproject",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.database.db_upgrade.alembic_upgrade",
                side_effect=RuntimeError("Can't locate revision identified by 'abc'"),
            ),
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_upgrade, ["auth"])

        assert result.exit_code != 0
        # Per-feature failure line is emitted to stdout.
        assert "❌" in result.output
        assert "auth" in result.output
        # ClickException summary goes to stderr.
        assert "Migration upgrade failed" in (result.output + result.stderr)
        assert "Traceback" not in (result.output + result.stderr)

    def test_upgrade_missing_models_skipped_silently(self, tmp_path, monkeypatch):
        """A feature whose migrations import a missing 'models' module is
        skipped, not treated as a hard failure."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        mdir = _make_versions_dir(tmp_path, "auth")

        with (
            patch(
                "splent_cli.commands.database.db_upgrade."
                "MigrationManager.get_feature_migration_dir",
                return_value=mdir,
            ),
            patch(
                "splent_cli.commands.database.db_upgrade.get_features_from_pyproject",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.database.db_upgrade.alembic_upgrade",
                side_effect=ImportError("No module named 'models'"),
            ),
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_upgrade, ["auth"])

        assert result.exit_code == 0
        assert "Migration upgrade failed" not in (result.output + result.stderr)
        assert "Traceback" not in (result.output + result.stderr)

    def test_upgrade_happy_path_reports_revision(self, tmp_path, monkeypatch):
        """A successful upgrade reports the feature and its revision."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        mdir = _make_versions_dir(tmp_path, "auth")

        fake_app = MagicMock()

        with (
            patch("splent_cli.commands.database.db_upgrade.current_app", fake_app),
            patch(
                "splent_cli.commands.database.db_upgrade."
                "MigrationManager.get_feature_migration_dir",
                return_value=mdir,
            ),
            patch(
                "splent_cli.commands.database.db_upgrade.get_features_from_pyproject",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.database.db_upgrade.alembic_upgrade",
                return_value=None,
            ),
            patch(
                "splent_cli.commands.database.db_upgrade."
                "MigrationManager.get_current_feature_revision",
                return_value="rev123",
            ),
            patch(
                "splent_cli.commands.database.db_upgrade."
                "MigrationManager.update_feature_status",
                return_value=None,
            ),
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_upgrade, ["auth"])

        assert result.exit_code == 0
        assert "auth -> rev123" in result.output
        assert "❌" not in result.output
        assert "Traceback" not in result.output

    def test_no_dirs_reports_warning_not_failure(self, tmp_path, monkeypatch):
        """With no feature migration dirs at all, db:upgrade warns and exits
        cleanly (no false failure)."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        with patch(
            "splent_cli.commands.database.db_upgrade."
            "MigrationManager.get_all_feature_migration_dirs",
            return_value={},
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_upgrade, [])

        assert result.exit_code == 0
        assert "No feature migrations" in result.output
        assert "Traceback" not in result.output

    def test_missing_feature_dir_clean_error(self, tmp_path, monkeypatch):
        """An unknown feature produces a clean error, not a traceback."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        with patch(
            "splent_cli.commands.database.db_upgrade."
            "MigrationManager.get_feature_migration_dir",
            return_value=None,
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_upgrade, ["ghost"])

        assert result.exit_code == 1
        assert "No migrations directory" in result.output
        assert "Traceback" not in result.output

    def test_no_product_selected_aborts(self, tmp_path, monkeypatch):
        """db:upgrade requires a selected product (SPLENT_APP)."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("SPLENT_APP", raising=False)

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(db_upgrade, [])
        assert result.exit_code == 1
        assert "No product selected" in result.output
