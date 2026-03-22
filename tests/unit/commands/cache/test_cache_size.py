"""
Tests for the cache:size command.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.cache.cache_size import cache_size, _human, _dir_size


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_cache_entry(workspace, namespace, dir_name, file_content=b"x" * 100):
    entry = workspace / ".splent_cache" / "features" / namespace / dir_name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "data.bin").write_bytes(file_content)
    return entry


# ---------------------------------------------------------------------------
# _human() helper — pure logic
# ---------------------------------------------------------------------------

class TestHuman:
    def test_bytes(self):
        assert "B" in _human(500)

    def test_kilobytes(self):
        assert "KB" in _human(2048)

    def test_megabytes(self):
        assert "MB" in _human(2 * 1024 * 1024)

    def test_gigabytes(self):
        assert "GB" in _human(2 * 1024 * 1024 * 1024)


# ---------------------------------------------------------------------------
# _dir_size() helper
# ---------------------------------------------------------------------------

class TestDirSize:
    def test_returns_total_file_sizes(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"x" * 100)
        (tmp_path / "b.txt").write_bytes(b"y" * 200)
        assert _dir_size(tmp_path) == 300

    def test_recurses_subdirectories(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "file.txt").write_bytes(b"z" * 50)
        assert _dir_size(tmp_path) == 50

    def test_empty_dir_returns_zero(self, tmp_path):
        assert _dir_size(tmp_path) == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCacheSizeCommand:
    def test_empty_cache_shows_info(self, runner, workspace):
        result = runner.invoke(cache_size, [])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_no_namespace_dirs_shows_info(self, runner, workspace):
        """Cache root exists but no namespace subdirs."""
        (workspace / ".splent_cache" / "features").mkdir(parents=True)
        result = runner.invoke(cache_size, [])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_shows_namespace_name(self, runner, workspace):
        _make_cache_entry(workspace, "splent_io", "splent_feature_auth@v1.0.0")
        result = runner.invoke(cache_size, [])
        assert result.exit_code == 0
        assert "splent_io" in result.output

    def test_shows_feature_name(self, runner, workspace):
        _make_cache_entry(workspace, "splent_io", "splent_feature_auth@v1.0.0")
        result = runner.invoke(cache_size, [])
        assert "splent_feature_auth" in result.output

    def test_shows_version_label(self, runner, workspace):
        _make_cache_entry(workspace, "splent_io", "splent_feature_auth@v1.0.0")
        result = runner.invoke(cache_size, [])
        assert "v1.0.0" in result.output

    def test_shows_editable_label(self, runner, workspace):
        _make_cache_entry(workspace, "splent_io", "splent_feature_auth")  # no @version
        result = runner.invoke(cache_size, [])
        assert "editable" in result.output

    def test_shows_total(self, runner, workspace):
        _make_cache_entry(workspace, "splent_io", "splent_feature_auth@v1.0.0")
        result = runner.invoke(cache_size, [])
        assert "Total" in result.output

    def test_multiple_entries(self, runner, workspace):
        _make_cache_entry(workspace, "splent_io", "splent_feature_auth@v1.0.0")
        _make_cache_entry(workspace, "splent_io", "splent_feature_payments@v2.0.0")
        result = runner.invoke(cache_size, [])
        assert "splent_feature_auth" in result.output
        assert "splent_feature_payments" in result.output
