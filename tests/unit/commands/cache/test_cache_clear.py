"""
Tests for the cache:clear command.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.cache.cache_clear import cache_clear, _remove_broken_symlinks


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_cache(workspace, namespace="splent_io", name="splent_feature_auth", version="v1.0.0"):
    path = workspace / ".splent_cache" / "features" / namespace / f"{name}@{version}"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# _remove_broken_symlinks() helper
# ---------------------------------------------------------------------------

class TestRemoveBrokenSymlinks:
    def test_removes_broken_symlinks(self, tmp_path):
        features_dir = tmp_path / "test_app" / "features" / "splent_io"
        features_dir.mkdir(parents=True)
        # Create a broken symlink
        broken = features_dir / "splent_feature_auth"
        broken.symlink_to(tmp_path / "nonexistent")
        assert broken.is_symlink()

        removed = _remove_broken_symlinks(tmp_path)
        assert removed == 1
        assert not broken.exists()

    def test_ignores_valid_symlinks(self, tmp_path):
        real_target = tmp_path / "real_feature"
        real_target.mkdir()
        features_dir = tmp_path / "test_app" / "features" / "splent_io"
        features_dir.mkdir(parents=True)
        link = features_dir / "splent_feature_auth"
        link.symlink_to(real_target)

        removed = _remove_broken_symlinks(tmp_path)
        assert removed == 0
        assert link.exists()

    def test_returns_zero_when_no_symlinks(self, tmp_path):
        assert _remove_broken_symlinks(tmp_path) == 0


# ---------------------------------------------------------------------------
# No cache directory
# ---------------------------------------------------------------------------

class TestNoCacheDirectory:
    def test_exits_0_with_warning_when_no_cache(self, runner, workspace):
        result = runner.invoke(cache_clear, ["--yes"])
        assert result.exit_code == 0
        assert "No .splent_cache" in result.output


# ---------------------------------------------------------------------------
# Clear entire cache (--yes)
# ---------------------------------------------------------------------------

class TestClearAll:
    def test_clears_entire_cache_with_yes(self, runner, workspace):
        _make_cache(workspace)
        result = runner.invoke(cache_clear, ["--yes"])
        assert result.exit_code == 0
        assert "Cleared" in result.output
        # Cache root should have been recreated (empty)
        cache_root = workspace / ".splent_cache" / "features"
        assert cache_root.exists()
        assert list(cache_root.iterdir()) == []

    def test_cancel_at_prompt_does_not_clear(self, runner, workspace):
        entry = _make_cache(workspace)
        result = runner.invoke(cache_clear, [], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        assert entry.exists()

    def test_confirm_at_prompt_clears(self, runner, workspace):
        _make_cache(workspace)
        result = runner.invoke(cache_clear, [], input="y\n")
        assert result.exit_code == 0
        assert "Cleared" in result.output


# ---------------------------------------------------------------------------
# --namespace
# ---------------------------------------------------------------------------

class TestNamespaceFilter:
    def test_clears_specific_namespace(self, runner, workspace):
        entry = _make_cache(workspace, namespace="splent_io")
        result = runner.invoke(cache_clear, ["--namespace", "splent_io", "--yes"])
        assert result.exit_code == 0
        assert "splent_io" in result.output
        assert not entry.parent.exists()

    def test_missing_namespace_exits_0(self, runner, workspace):
        _make_cache(workspace, namespace="splent_io")
        result = runner.invoke(cache_clear, ["--namespace", "missing_ns", "--yes"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# --feature
# ---------------------------------------------------------------------------

class TestFeatureFilter:
    def test_clears_matching_feature_entries(self, runner, workspace):
        entry = _make_cache(workspace, name="splent_feature_auth", version="v1.0.0")
        result = runner.invoke(cache_clear, ["--feature", "splent_feature_auth", "--yes"])
        assert result.exit_code == 0
        assert "Cleared" in result.output
        assert not entry.exists()

    def test_missing_feature_exits_0(self, runner, workspace):
        _make_cache(workspace, name="splent_feature_auth")
        result = runner.invoke(cache_clear, ["--feature", "splent_feature_missing", "--yes"])
        assert result.exit_code == 0
        assert "No cache entries" in result.output


# ---------------------------------------------------------------------------
# Broken symlink cleanup reported
# ---------------------------------------------------------------------------

class TestBrokenSymlinkReport:
    def test_reports_removed_symlinks(self, runner, workspace):
        _make_cache(workspace)
        features_dir = workspace / "test_app" / "features" / "splent_io"
        features_dir.mkdir(parents=True)
        broken = features_dir / "splent_feature_auth"
        broken.symlink_to(workspace / "nonexistent")

        result = runner.invoke(cache_clear, ["--yes"])
        assert result.exit_code == 0
        assert "broken" in result.output.lower() or "symlink" in result.output.lower()

    def test_reports_no_symlinks_when_clean(self, runner, workspace):
        _make_cache(workspace)
        result = runner.invoke(cache_clear, ["--yes"])
        assert result.exit_code == 0
        assert "No broken symlinks" in result.output
