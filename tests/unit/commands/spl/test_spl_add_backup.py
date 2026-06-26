"""Regression tests for the hardened UVL-editing behavior of the spl:* commands.

Covers spl:add-feature and spl:add-constraints, focusing on the safety net
added during hardening:

  * editing a UVL backs up the original before writing,
  * the result is re-parsed/validated after the atomic write,
  * a malformed / unexpected UVL layout is reported clearly and the original
    file is left untouched (or restored) rather than silently corrupted.

No docker / git / network / DB. The commands resolve the UVL purely from disk
(``WORKING_DIR``/splent_catalog/<spl>/<spl>.uvl), so once that file exists no
network fetch happens. Both commands are @requires_detached → SPLENT_APP must
be unset (the ``workspace`` fixture in conftest unsets it).
"""

import pytest

from splent_cli.commands.spl.spl_add_feature import (
    spl_add_feature,
    _parse_uvl_packages,
)
from splent_cli.commands.spl.spl_add_constraints import (
    spl_fix,
    _parse_uvl,
    _write_constraints,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SPL = "sample_spl"

WELL_FORMED_UVL = """\
features
\tSamplePlatform
\t\tmandatory
\t\t\tauth {org 'splent-io', package 'splent_feature_auth'}
\t\toptional
\t\t\tnotes {org 'splent-io', package 'splent_feature_notes'}
constraints
\tnotes => auth
"""


def _make_uvl(workspace, text, spl_name=SPL):
    """Write a UVL file at the location _resolve_spl expects and return its path."""
    uvl_dir = workspace / "splent_catalog" / spl_name
    uvl_dir.mkdir(parents=True, exist_ok=True)
    uvl_path = uvl_dir / f"{spl_name}.uvl"
    uvl_path.write_text(text, encoding="utf-8")
    return uvl_path


def _make_feature(workspace, pkg, *, models_src="", py_src=""):
    """Create a minimal feature dir at workspace root with src layout."""
    src_dir = workspace / pkg / "src" / "splent_io" / pkg
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    if models_src:
        (src_dir / "models.py").write_text(models_src, encoding="utf-8")
    if py_src:
        (src_dir / "routes.py").write_text(py_src, encoding="utf-8")
    return workspace / pkg


# ===========================================================================
# spl:add-feature — hardened behaviors
# ===========================================================================


class TestAddFeatureBackupAndValidate:
    def test_backup_created_and_result_parseable(self, workspace, runner):
        """A successful edit creates a .bak of the original and the written
        UVL re-parses to include the new feature package."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        _make_feature(workspace, "splent_feature_tags")

        result = runner.invoke(
            spl_add_feature,
            [SPL, "splent_feature_tags"],
            input="y\n",
        )

        assert result.exit_code == 0, result.output
        assert "Traceback" not in result.output
        assert "Traceback" not in (result.stderr or "")

        # The new feature actually landed and the file still parses.
        written = _parse_uvl_packages(str(uvl_path))
        assert "splent_feature_tags" in set(written.values())
        # Original features survive the edit.
        assert "splent_feature_auth" in set(written.values())
        assert "splent_feature_notes" in set(written.values())

        # A backup of the original content was created during the write.
        bak = uvl_path.with_name(uvl_path.name + ".bak")
        assert bak.exists()
        assert "splent_feature_tags" not in bak.read_text()
        assert "splent_feature_auth" in bak.read_text()

    def test_idempotent_when_already_declared_no_write(self, workspace, runner):
        """Re-adding an already-declared feature is a no-op: no backup, file
        bytes unchanged, clean message."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        before = uvl_path.read_bytes()

        result = runner.invoke(spl_add_feature, [SPL, "splent_feature_notes"])

        assert result.exit_code == 0, result.output
        assert "already declared" in result.output
        assert uvl_path.read_bytes() == before
        assert not uvl_path.with_name(uvl_path.name + ".bak").exists()

    def test_cancelled_confirm_leaves_file_untouched(self, workspace, runner):
        """Declining the confirmation prompt must not edit or back up the UVL."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        _make_feature(workspace, "splent_feature_tags")
        before = uvl_path.read_bytes()

        result = runner.invoke(
            spl_add_feature, [SPL, "splent_feature_tags"], input="n\n"
        )

        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        assert uvl_path.read_bytes() == before
        assert not uvl_path.with_name(uvl_path.name + ".bak").exists()

    def test_missing_feature_dir_clean_error(self, workspace, runner):
        """A feature not present at workspace root is reported clearly with a
        non-zero exit and no traceback; the UVL is never touched."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        before = uvl_path.read_bytes()

        result = runner.invoke(spl_add_feature, [SPL, "splent_feature_ghost"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()
        assert "Traceback" not in result.output
        assert uvl_path.read_bytes() == before

    def test_malformed_uvl_no_constraints_section_refuses(self, workspace, runner):
        """When dependencies are detected but the UVL has no 'constraints'
        header on its own line, the command must refuse to edit (clean error,
        original bytes intact) rather than silently corrupt the model."""
        # auth owns the 'user' table; tags references it via FK → dependency
        # detected. The UVL deliberately has no 'constraints' section.
        malformed = """\
features
\tSamplePlatform
\t\tmandatory
\t\t\tauth {org 'splent-io', package 'splent_feature_auth'}
"""
        uvl_path = _make_uvl(workspace, malformed)
        before = uvl_path.read_bytes()

        _make_feature(
            workspace,
            "splent_feature_auth",
            models_src="class User(db.Model):\n    pass\n",
        )
        _make_feature(
            workspace,
            "splent_feature_tags",
            py_src='fk = db.ForeignKey("user.id")\n',
        )

        result = runner.invoke(
            spl_add_feature, [SPL, "splent_feature_tags"], input="y\n"
        )

        assert result.exit_code == 1
        out = result.output
        assert "constraints" in out.lower()
        assert "Traceback" not in out
        # Refused before touching the file → bytes identical, no backup left.
        assert uvl_path.read_bytes() == before
        assert not uvl_path.with_name(uvl_path.name + ".bak").exists()


# ===========================================================================
# spl:add-constraints — _write_constraints unit-level hardening
# ===========================================================================


class TestWriteConstraintsHardening:
    def test_backup_then_restore_on_failed_validation(self, workspace, monkeypatch):
        """If post-write re-parse does not find the new constraint, the original
        file is restored from backup and the error is surfaced (not swallowed)."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        _, _, raw = _parse_uvl(str(uvl_path))
        original = uvl_path.read_text()

        # Force validation to "lose" the constraint by making the re-parse
        # return no constraints, simulating a corrupted/unexpected write.
        import splent_cli.commands.spl.spl_add_constraints as mod

        monkeypatch.setattr(mod, "_parse_uvl", lambda p: ({}, [], ""))

        with pytest.raises(Exception) as exc:
            _write_constraints(str(uvl_path), raw, ["tags => notes"])

        # The exception message names the missing constraint.
        assert "tags => notes" in str(exc.value)
        # The good file was restored, not left corrupted.
        assert uvl_path.read_text() == original
        # Backup is cleaned up (finally block unlinks it).
        assert not uvl_path.with_name(uvl_path.name + ".bak").exists()

    def test_successful_write_appends_and_validates(self, workspace):
        """Happy path: a new constraint is appended, the file re-parses to
        contain it, and the temporary backup is removed afterward."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        _, _, raw = _parse_uvl(str(uvl_path))

        _write_constraints(str(uvl_path), raw, ["notes => extra"])

        _, constraints, _ = _parse_uvl(str(uvl_path))
        assert "notes => extra" in constraints
        # The pre-existing constraint is preserved.
        assert "notes => auth" in constraints
        # Backup removed on success.
        assert not uvl_path.with_name(uvl_path.name + ".bak").exists()

    def test_backup_restored_on_atomic_write_crash(self, workspace, monkeypatch):
        """If the atomic write itself raises mid-operation, the original file
        is restored from the backup — never left half-written."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        _, _, raw = _parse_uvl(str(uvl_path))
        original = uvl_path.read_text()

        import splent_cli.commands.spl.spl_add_constraints as mod

        calls = {"n": 0}

        def boom(path, content, **kw):
            # First call = the real write attempt → crash.
            # Subsequent call = the restore-from-backup → let it through.
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("disk full")
            return original_atomic(path, content, **kw)

        from splent_cli.utils.io_utils import atomic_write as original_atomic

        monkeypatch.setattr(mod, "atomic_write", boom)

        with pytest.raises(RuntimeError, match="disk full"):
            _write_constraints(str(uvl_path), raw, ["notes => extra"])

        # Original content intact via restore path.
        assert uvl_path.read_text() == original
        assert not uvl_path.with_name(uvl_path.name + ".bak").exists()


# ===========================================================================
# spl:add-constraints — command happy path
# ===========================================================================


class TestAddConstraintsCommand:
    def test_no_missing_constraints_reports_clean(self, workspace, runner):
        """When every cross-feature import already has a constraint, the command
        reports success and writes nothing."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)
        before = uvl_path.read_bytes()

        # notes imports auth, and the UVL already has 'notes => auth'.
        _make_feature(workspace, "splent_feature_auth")
        _make_feature(
            workspace,
            "splent_feature_notes",
            py_src="from splent_io.splent_feature_auth import models\n",
        )

        result = runner.invoke(spl_fix, [SPL])

        assert result.exit_code == 0, result.output
        assert "matching UVL constraints" in result.output
        assert uvl_path.read_bytes() == before

    def test_missing_constraint_added_and_validated(self, workspace, runner):
        """An undeclared cross-feature import is offered, accepted, written,
        and the resulting UVL re-parses to contain the new constraint."""
        uvl_path = _make_uvl(workspace, WELL_FORMED_UVL)

        # auth imports notes → constraint 'auth => notes' is undeclared.
        _make_feature(
            workspace,
            "splent_feature_auth",
            py_src="from splent_io.splent_feature_notes import models\n",
        )
        _make_feature(workspace, "splent_feature_notes")

        # Prompt: action 'add', then confirm 'y' to write.
        result = runner.invoke(spl_fix, [SPL], input="add\ny\n")

        assert result.exit_code == 0, result.output
        assert "Traceback" not in result.output
        assert "UVL updated" in result.output

        _, constraints, _ = _parse_uvl(str(uvl_path))
        assert "auth => notes" in constraints
        assert "notes => auth" in constraints  # pre-existing preserved
