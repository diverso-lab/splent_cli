"""Regression tests for the hardened robustness guards in
contract_freshness.py and template_drift.py.

Focus (hardened behaviors):
- contract_freshness._newest_source_mtime: a broken symlink / file removed
  mid-walk (getmtime raises OSError) is skipped, never crashes.
- contract_freshness._pyproject_mtime: a broken-symlink pyproject is handled.
- template_drift.file_diff: read_text raising OSError is swallowed (returns None),
  including the broken-symlink-on-disk case.
- template_drift.product_ctx: WORKING_DIR resolution degrades gracefully when
  unset / when the pyproject path is unreadable.
"""
import os

import pytest

from splent_cli.utils import contract_freshness
from splent_cli.utils.contract_freshness import (
    _newest_source_mtime,
    _pyproject_mtime,
    is_contract_stale,
)
from splent_cli.utils.template_drift import file_diff, product_ctx


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_feature(tmp_path, with_pyproject=True):
    """Create a minimal feature_dir layout: <feature>/pyproject.toml + src/."""
    feature_dir = tmp_path / "myfeat"
    src = feature_dir / "src"
    src.mkdir(parents=True)
    if with_pyproject:
        (feature_dir / "pyproject.toml").write_text("[project]\nname='x'\n")
    return feature_dir, src


# ---------------------------------------------------------------------------
# contract_freshness — broken symlink / missing file during walk (HARDENED)
# ---------------------------------------------------------------------------

class TestNewestSourceMtimeGuards:
    def test_broken_symlink_in_src_is_skipped_not_crash(self, tmp_path):
        _, src = _make_feature(tmp_path)
        # A real source file with a known mtime.
        real = src / "real.py"
        real.write_text("x = 1\n")
        # A broken symlink ending in .py — getmtime() will raise OSError.
        broken = src / "broken.py"
        os.symlink(str(src / "does_not_exist.py"), str(broken))
        assert os.path.islink(broken) and not os.path.exists(broken)

        # Must not raise, and must reflect the real file's mtime.
        result = _newest_source_mtime(str(src.parent))
        assert result == pytest.approx(os.path.getmtime(real))

    def test_only_broken_symlink_returns_zero(self, tmp_path):
        _, src = _make_feature(tmp_path)
        broken = src / "broken.html"
        os.symlink(str(src / "nope.html"), str(broken))
        # No readable source files at all -> falls back to 0.0, no crash.
        assert _newest_source_mtime(str(src.parent)) == 0.0

    def test_file_removed_mid_walk_is_skipped(self, tmp_path, monkeypatch):
        _, src = _make_feature(tmp_path)
        (src / "a.py").write_text("a\n")
        (src / "b.py").write_text("b\n")

        real_getmtime = os.path.getmtime

        def flaky_getmtime(path):
            if path.endswith("a.py"):
                raise OSError("vanished between walk and stat")
            return real_getmtime(path)

        monkeypatch.setattr(contract_freshness.os.path, "getmtime", flaky_getmtime)
        # b.py still counts; a.py is skipped silently.
        result = _newest_source_mtime(str(src.parent))
        assert result == pytest.approx(real_getmtime(str(src / "b.py")))

    def test_missing_src_dir_returns_zero(self, tmp_path):
        feature_dir = tmp_path / "nofeat"
        feature_dir.mkdir()
        assert _newest_source_mtime(str(feature_dir)) == 0.0


class TestPyprojectMtimeGuards:
    def test_missing_pyproject_returns_none(self, tmp_path):
        feature_dir, _ = _make_feature(tmp_path, with_pyproject=False)
        assert _pyproject_mtime(str(feature_dir)) is None

    def test_existing_pyproject_returns_mtime(self, tmp_path):
        feature_dir, _ = _make_feature(tmp_path)
        mt = _pyproject_mtime(str(feature_dir))
        assert mt == pytest.approx(
            os.path.getmtime(str(feature_dir / "pyproject.toml"))
        )

    def test_getmtime_oserror_returns_none(self, tmp_path, monkeypatch):
        feature_dir, _ = _make_feature(tmp_path)

        def boom(path):
            raise OSError("removed between isfile and getmtime")

        monkeypatch.setattr(contract_freshness.os.path, "getmtime", boom)
        # Hardened: race between isfile() and getmtime() yields None, not crash.
        assert _pyproject_mtime(str(feature_dir)) is None


class TestIsContractStaleGuards:
    def test_no_pyproject_is_not_stale(self, tmp_path):
        feature_dir, _ = _make_feature(tmp_path, with_pyproject=False)
        assert is_contract_stale(str(feature_dir)) is False

    def test_broken_symlink_source_does_not_crash(self, tmp_path):
        feature_dir, src = _make_feature(tmp_path)
        broken = src / "broken.py"
        os.symlink(str(src / "missing.py"), str(broken))
        # Walk skips the broken link; with no real newer source, not stale.
        assert is_contract_stale(str(feature_dir)) is False

    def test_newer_source_marks_stale(self, tmp_path):
        feature_dir, src = _make_feature(tmp_path)
        pyproject = feature_dir / "pyproject.toml"
        # Force pyproject into the past and a source file into the future.
        old = 1_000_000.0
        new = 2_000_000.0
        os.utime(str(pyproject), (old, old))
        f = src / "code.py"
        f.write_text("y = 2\n")
        os.utime(str(f), (new, new))
        assert is_contract_stale(str(feature_dir)) is True


# ---------------------------------------------------------------------------
# template_drift.file_diff — read_text OSError handled (HARDENED)
# ---------------------------------------------------------------------------

class TestFileDiffOSErrorGuards:
    def test_oserror_on_read_returns_none(self, tmp_path, monkeypatch):
        from pathlib import Path

        f = tmp_path / "f.txt"
        f.write_text("content\n")

        def boom(self, *a, **k):
            raise OSError("permission denied / is a directory")

        monkeypatch.setattr(Path, "read_text", boom)
        # Hardened: unreadable file is treated as "no diff", not an exception.
        assert file_diff(f, "anything\n") is None

    def test_broken_symlink_on_disk_returns_none(self, tmp_path):
        from pathlib import Path

        broken = tmp_path / "link.txt"
        os.symlink(str(tmp_path / "missing.txt"), str(broken))
        assert broken.is_symlink() and not broken.exists()
        # read_text on a broken symlink raises FileNotFoundError (a subclass of
        # OSError) -> handled, returns None.
        assert file_diff(Path(broken), "expected\n") is None

    def test_directory_path_returns_none(self, tmp_path):
        from pathlib import Path

        d = tmp_path / "adir"
        d.mkdir()
        # read_text on a directory raises OSError (IsADirectoryError) -> None.
        assert file_diff(Path(d), "expected\n") is None


# ---------------------------------------------------------------------------
# template_drift.product_ctx — WORKING_DIR resolution degrades gracefully
# ---------------------------------------------------------------------------

class TestProductCtxWorkingDirDegrades:
    def test_unset_working_dir_does_not_crash(self, monkeypatch):
        # No WORKING_DIR -> path is relative/garbage and pyproject open fails;
        # ctx must still build with empty spl_name.
        monkeypatch.delenv("WORKING_DIR", raising=False)
        ctx = product_ctx("myapp")
        assert ctx["product_name"] == "myapp"
        assert ctx["spl_name"] == ""

    def test_missing_pyproject_yields_empty_spl(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        # tmp_path/myapp/pyproject.toml does not exist.
        ctx = product_ctx("myapp")
        assert ctx["spl_name"] == ""
        assert isinstance(ctx["web_port"], int)

    def test_reads_spl_name_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        prod = tmp_path / "myapp"
        prod.mkdir()
        (prod / "pyproject.toml").write_bytes(
            b'[tool.splent]\nspl = "my_spl"\n'
        )
        ctx = product_ctx("myapp")
        assert ctx["spl_name"] == "my_spl"

    def test_unreadable_pyproject_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        prod = tmp_path / "myapp"
        prod.mkdir()
        # pyproject is a directory -> open() raises OSError, caught -> empty.
        (prod / "pyproject.toml").mkdir()
        ctx = product_ctx("myapp")
        assert ctx["spl_name"] == ""
