"""Tests for db:rollback — hardened safety guards.

Covered hardened behaviors:
  * A downgrade failure during cascade reports the partial-rollback state and
    a recovery hint, with NO raw traceback / CalledProcessError leaking out.
  * The migration-file deletion prompt defaults to NO and is clearly labeled
    IRREVERSIBLE; declining it deletes nothing.

Everything that touches the database / alembic / the lifecycle state machine is
mocked at the module boundary — no real docker / git / network / DB required.
"""

import contextlib
import os
from unittest.mock import patch, MagicMock

from flask import Flask
from click.testing import CliRunner

from splent_cli.commands.database.db_rollback import db_rollback


@contextlib.contextmanager
def _flask_ctx():
    """Push a minimal Flask app context with a fake `migrate` extension.

    The command reads `current_app` and `app.extensions["migrate"].db.engine`;
    a real engine is never used because MigrationManager is mocked.
    """
    app = Flask(__name__)
    migrate = MagicMock()
    migrate.db.engine = MagicMock()
    app.extensions["migrate"] = migrate
    with app.app_context():
        yield app


@contextlib.contextmanager
def _patched(
    monkeypatch,
    tmp_path,
    *,
    declared,
    mdir,
    migration_manager,
    downgrade,
    find_dependents,
):
    """Patch every module-level boundary db_rollback reaches into."""
    monkeypatch.setenv("SPLENT_APP", "test_app")
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    mod = "splent_cli.commands.database.db_rollback"
    with (
        _flask_ctx(),
        patch(f"{mod}.PathUtils.get_app_base_dir", return_value=str(tmp_path)),
        patch(f"{mod}.get_features_from_pyproject", return_value=declared),
        patch(f"{mod}.MigrationManager", migration_manager),
        patch(f"{mod}.alembic_downgrade", downgrade),
        patch(f"{mod}._find_dependents", return_value=find_dependents),
        patch(f"{mod}.advance_state"),
        patch(
            f"{mod}.resolve_feature_key_from_entry",
            side_effect=lambda e: (e, "ns", e, "1.0.0"),
        ),
    ):
        yield


def _make_migration_manager(
    *, mdir, revision_after, all_status=None, dep_revisions=None
):
    """Build a MagicMock standing in for the MigrationManager class.

    `dep_revisions` maps dependent feature name -> current revision (used by
    get_current_feature_revision); the target feature returns `revision_after`.
    """
    dep_revisions = dep_revisions or {}
    mm = MagicMock()
    mm.get_feature_migration_dir.side_effect = lambda f: mdir.get(f)
    mm.get_all_status.return_value = all_status or []

    def _current_rev(feature, _engine):
        if feature in dep_revisions:
            return dep_revisions[feature]
        return revision_after

    mm.get_current_feature_revision.side_effect = _current_rev
    return mm


class TestCascadePartialRollbackRecovery:
    """A dependent downgrade failure must surface state + recovery, cleanly."""

    def test_dependent_failure_reports_partial_state_no_traceback(
        self, tmp_path, monkeypatch
    ):
        # Two dependents are applied; rolling the second one back blows up.
        # Reverse order => first dependent processed is "dep_b", then "dep_a"
        # fails, leaving dep_b already rolled back (unrecoverable mid-flight).
        feature = "splent_feature_core"
        dep_a = "splent_feature_a"
        dep_b = "splent_feature_b"

        target_dir = tmp_path / "core_migs"
        target_dir.mkdir()
        a_dir = tmp_path / "a_migs"
        a_dir.mkdir()
        b_dir = tmp_path / "b_migs"
        b_dir.mkdir()
        mdir = {feature: str(target_dir), dep_a: str(a_dir), dep_b: str(b_dir)}

        mm = _make_migration_manager(
            mdir=mdir,
            revision_after="rev_target",
            dep_revisions={dep_a: "rev_a", dep_b: "rev_b"},
        )

        calls = {"n": 0}

        def fake_downgrade(directory=None, revision=None):
            calls["n"] += 1
            # First downgrade (dep_b, processed first in reversed order) ok;
            # second (dep_a) raises a low-level error like alembic would.
            if calls["n"] == 1:
                return None
            raise RuntimeError("alembic: target database is not up to date")

        with _patched(
            monkeypatch,
            tmp_path,
            declared=[feature, dep_a, dep_b],
            mdir=mdir,
            migration_manager=mm,
            downgrade=fake_downgrade,
            find_dependents=[dep_a, dep_b],
        ):
            runner = CliRunner(mix_stderr=False)
            # cascade=True so it does not prompt; we exercise the failure path.
            result = runner.invoke(db_rollback, [feature, "--cascade"])

        assert result.exit_code == 1
        combined = result.output + result.stderr
        # Hardened: partial-rollback state announced + recovery instructions.
        assert "Partial rollback" in combined
        assert feature in combined and "NOT rolled back" in combined
        # The already-rolled-back dependent must be named so the user can recover
        assert dep_b in combined
        assert "recover" in combined.lower() or "re-apply" in combined.lower()
        # No raw exception plumbing leaked to the user.
        assert "Traceback" not in combined
        assert "RuntimeError" not in combined
        # The target feature must NOT have been rolled back after the failure.
        assert calls["n"] == 2

    def test_target_failure_after_dependents_explains_mixed_state(
        self, tmp_path, monkeypatch
    ):
        # Dependent rolls back fine, then the TARGET downgrade fails.
        feature = "splent_feature_core"
        dep = "splent_feature_a"
        target_dir = tmp_path / "core_migs"
        target_dir.mkdir()
        dep_dir = tmp_path / "a_migs"
        dep_dir.mkdir()
        mdir = {feature: str(target_dir), dep: str(dep_dir)}

        mm = _make_migration_manager(
            mdir=mdir,
            revision_after="rev_target",
            dep_revisions={dep: "rev_a"},
        )

        calls = {"n": 0}

        def fake_downgrade(directory=None, revision=None):
            calls["n"] += 1
            if directory == str(target_dir):
                raise RuntimeError("alembic boom on target")
            return None

        with _patched(
            monkeypatch,
            tmp_path,
            declared=[feature, dep],
            mdir=mdir,
            migration_manager=mm,
            downgrade=fake_downgrade,
            find_dependents=[dep],
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_rollback, [feature, "--cascade"])

        assert result.exit_code == 1
        combined = result.output + result.stderr
        assert "Partial rollback" in combined
        assert "mixed state" in combined.lower()
        assert "Traceback" not in combined
        assert "RuntimeError" not in combined


class TestMigrationFileDeletionPrompt:
    """The destructive file-deletion offer must be opt-in + flagged irreversible."""

    def _setup_rolled_to_base(self, tmp_path, monkeypatch, *, n_files=2):
        """Roll the target feature fully back to base with migration files left."""
        feature = "splent_feature_core"
        target_dir = tmp_path / "core_migs"
        versions = target_dir / "versions"
        versions.mkdir(parents=True)
        for i in range(n_files):
            (versions / f"00{i}_rev.py").write_text("# migration\n")
        # An __init__.py / dunder file must be ignored by the deletion logic.
        (versions / "__init__.py").write_text("")
        mdir = {feature: str(target_dir)}
        mm = _make_migration_manager(
            mdir=mdir,
            revision_after=None,  # None => rolled back to base
        )
        return feature, target_dir, versions, mdir, mm

    def test_decline_deletes_nothing_and_warns_irreversible(
        self, tmp_path, monkeypatch
    ):
        feature, target_dir, versions, mdir, mm = self._setup_rolled_to_base(
            tmp_path, monkeypatch
        )
        before = sorted(os.listdir(versions))

        with _patched(
            monkeypatch,
            tmp_path,
            declared=[feature],
            mdir=mdir,
            migration_manager=mm,
            downgrade=MagicMock(return_value=None),
            find_dependents=[],
        ):
            runner = CliRunner(mix_stderr=False)
            # Empty stdin => accept defaults. The prompt must default to NO.
            result = runner.invoke(db_rollback, [feature], input="\n")

        assert result.exit_code == 0
        combined = result.output + result.stderr
        # Hardened: warning is shown and labeled irreversible.
        assert "IRREVERSIBLE" in combined or "irreversible" in combined.lower()
        # Defaulting (blank input) must NOT delete the files.
        assert sorted(os.listdir(versions)) == before
        assert "deleted" not in result.output.lower()

    def test_default_is_no(self, tmp_path, monkeypatch):
        feature, target_dir, versions, mdir, mm = self._setup_rolled_to_base(
            tmp_path, monkeypatch
        )

        with _patched(
            monkeypatch,
            tmp_path,
            declared=[feature],
            mdir=mdir,
            migration_manager=mm,
            downgrade=MagicMock(return_value=None),
            find_dependents=[],
        ):
            runner = CliRunner(mix_stderr=False)
            # Verify the actual prompt default by inspecting click.confirm.
            with patch(
                "splent_cli.commands.database.db_rollback.click.confirm",
                wraps=__import__("click").confirm,
            ) as spy:
                result = runner.invoke(db_rollback, [feature], input="\n")

        assert result.exit_code == 0
        # The deletion confirm must be invoked with default=False.
        delete_calls = [
            c
            for c in spy.call_args_list
            if "delete" in (str(c.args[0]) if c.args else "").lower()
        ]
        assert delete_calls, "expected a deletion confirmation prompt"
        for c in delete_calls:
            assert c.kwargs.get("default") is False

    def test_confirm_yes_deletes_only_migration_files(self, tmp_path, monkeypatch):
        feature, target_dir, versions, mdir, mm = self._setup_rolled_to_base(
            tmp_path, monkeypatch
        )

        with _patched(
            monkeypatch,
            tmp_path,
            declared=[feature],
            mdir=mdir,
            migration_manager=mm,
            downgrade=MagicMock(return_value=None),
            find_dependents=[],
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_rollback, [feature], input="y\n")

        assert result.exit_code == 0
        # Only the real migration files are removed; __init__.py is preserved.
        remaining = sorted(os.listdir(versions))
        assert remaining == ["__init__.py"]
        assert "deleted" in result.output.lower()


class TestCoreHappyPath:
    """A couple of core, non-destructive paths for the module."""

    def test_undeclared_feature_without_db_entry_exits_cleanly(
        self, tmp_path, monkeypatch
    ):
        mm = _make_migration_manager(mdir={}, revision_after=None, all_status=[])
        with _patched(
            monkeypatch,
            tmp_path,
            declared=["other_feature"],
            mdir={},
            migration_manager=mm,
            downgrade=MagicMock(),
            find_dependents=[],
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_rollback, ["splent_feature_ghost"])

        assert result.exit_code == 1
        combined = result.output + result.stderr
        assert "not declared" in combined.lower()
        assert "Traceback" not in combined

    def test_simple_rollback_not_to_base_does_not_offer_deletion(
        self, tmp_path, monkeypatch
    ):
        feature = "splent_feature_core"
        target_dir = tmp_path / "core_migs"
        versions = target_dir / "versions"
        versions.mkdir(parents=True)
        (versions / "001_rev.py").write_text("# migration\n")
        mdir = {feature: str(target_dir)}
        # revision_after is a real revision (not None) => NOT fully at base.
        mm = _make_migration_manager(mdir=mdir, revision_after="rev_keep")

        with _patched(
            monkeypatch,
            tmp_path,
            declared=[feature],
            mdir=mdir,
            migration_manager=mm,
            downgrade=MagicMock(return_value=None),
            find_dependents=[],
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_rollback, [feature])

        assert result.exit_code == 0
        combined = result.output + result.stderr
        # No deletion offer when not fully rolled back to base.
        assert "irreversible" not in combined.lower()
        # Migration file untouched.
        assert os.path.exists(versions / "001_rev.py")
        # Status update was recorded for the target feature.
        mm.update_feature_status.assert_called_once()

    def test_feature_with_no_migrations_is_noop(self, tmp_path, monkeypatch):
        feature = "splent_feature_core"
        # No migration dir on disk.
        mm = _make_migration_manager(mdir={feature: None}, revision_after=None)
        with _patched(
            monkeypatch,
            tmp_path,
            declared=[feature],
            mdir={feature: None},
            migration_manager=mm,
            downgrade=MagicMock(),
            find_dependents=[],
        ):
            runner = CliRunner(mix_stderr=False)
            result = runner.invoke(db_rollback, [feature])

        assert result.exit_code == 0
        combined = result.output + result.stderr
        assert "nothing to roll back" in combined.lower()
