"""Tests for cache_utils.py filesystem permission helpers."""
import os
import stat
import pytest

from splent_cli.utils.cache_utils import make_feature_readonly, make_feature_writable


def _is_writable(path: str) -> bool:
    return bool(os.stat(path).st_mode & stat.S_IWUSR)


def _is_readable(path: str) -> bool:
    return bool(os.stat(path).st_mode & stat.S_IRUSR)


def _make_feature_dir(tmp_path, files=None):
    """Create a minimal feature directory with given file names."""
    feat = tmp_path / "splent_feature_auth@v1.0.0"
    feat.mkdir()
    for name in (files or ["module.py", "models.py"]):
        (feat / name).write_text("# content")
    return feat


class TestMakeFeatureReadonly:
    def test_files_become_readonly(self, tmp_path):
        feat = _make_feature_dir(tmp_path)
        make_feature_readonly(str(feat))
        for f in feat.iterdir():
            assert not _is_writable(str(f)), f"{f} should be read-only"

    def test_files_remain_readable(self, tmp_path):
        feat = _make_feature_dir(tmp_path)
        make_feature_readonly(str(feat))
        for f in feat.iterdir():
            assert _is_readable(str(f)), f"{f} should still be readable"

    def test_skips_git_internals(self, tmp_path):
        feat = _make_feature_dir(tmp_path, files=["module.py"])
        git_dir = feat / ".git"
        git_dir.mkdir()
        git_file = git_dir / "HEAD"
        git_file.write_text("ref: refs/heads/main")
        make_feature_readonly(str(feat))
        # .git/HEAD should NOT have been modified (still writable)
        assert _is_writable(str(git_file))

    def test_nested_files_become_readonly(self, tmp_path):
        feat = tmp_path / "feature"
        subdir = feat / "src" / "pkg"
        subdir.mkdir(parents=True)
        nested = subdir / "code.py"
        nested.write_text("x = 1")
        make_feature_readonly(str(feat))
        assert not _is_writable(str(nested))

    def test_empty_directory_does_not_crash(self, tmp_path):
        feat = tmp_path / "empty_feature"
        feat.mkdir()
        make_feature_readonly(str(feat))  # must not raise

    def test_nonexistent_path_does_not_crash(self, tmp_path):
        make_feature_readonly(str(tmp_path / "nonexistent"))  # must not raise


class TestMakeFeatureWritable:
    def test_readonly_files_become_writable(self, tmp_path):
        feat = _make_feature_dir(tmp_path)
        make_feature_readonly(str(feat))
        make_feature_writable(str(feat))
        for f in feat.iterdir():
            assert _is_writable(str(f)), f"{f} should be writable again"

    def test_skips_git_internals(self, tmp_path):
        feat = tmp_path / "feature"
        feat.mkdir()
        git_dir = feat / ".git"
        git_dir.mkdir()
        git_file = git_dir / "config"
        git_file.write_text("[core]")
        original_mode = os.stat(str(git_file)).st_mode
        make_feature_writable(str(feat))
        assert os.stat(str(git_file)).st_mode == original_mode

    def test_nested_files_become_writable(self, tmp_path):
        feat = tmp_path / "feature"
        subdir = feat / "src" / "pkg"
        subdir.mkdir(parents=True)
        nested = subdir / "code.py"
        nested.write_text("x = 1")
        make_feature_readonly(str(feat))
        make_feature_writable(str(feat))
        assert _is_writable(str(nested))

    def test_empty_directory_does_not_crash(self, tmp_path):
        feat = tmp_path / "empty_feature"
        feat.mkdir()
        make_feature_writable(str(feat))  # must not raise

    def test_roundtrip_readonly_then_writable(self, tmp_path):
        feat = _make_feature_dir(tmp_path, files=["a.py", "b.py"])
        make_feature_readonly(str(feat))
        for f in feat.iterdir():
            assert not _is_writable(str(f))
        make_feature_writable(str(feat))
        for f in feat.iterdir():
            assert _is_writable(str(f))
