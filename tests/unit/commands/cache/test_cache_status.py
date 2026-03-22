"""
Tests for the cache:status command.

Pattern: real filesystem via tmp_path + monkeypatch WORKING_DIR.
No subprocess mocking needed — this command only walks directories.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.cache.cache_status import cache_status
from tests.conftest import make_cache_entry


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Empty cache
# ---------------------------------------------------------------------------

class TestEmptyCache:
    def test_no_cache_dir(self, runner, workspace):
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_cache_dir_exists_but_empty(self, runner, workspace):
        (workspace / ".splent_cache" / "features").mkdir(parents=True)
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()


# ---------------------------------------------------------------------------
# Versioned features
# ---------------------------------------------------------------------------

class TestVersionedFeatures:
    def test_single_versioned_feature(self, runner, workspace):
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "v1.0.0" in result.output

    def test_multiple_versions(self, runner, workspace):
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v2.0.0")
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "v1.0.0" in result.output
        assert "v2.0.0" in result.output

    def test_multiple_features(self, runner, workspace):
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        make_cache_entry(workspace, "splent_io", "splent_feature_profile", "v1.0.0")
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "splent_feature_profile" in result.output


# ---------------------------------------------------------------------------
# Editable features
# ---------------------------------------------------------------------------

class TestEditableFeatures:
    def test_editable_shown(self, runner, workspace):
        make_cache_entry(workspace, "splent_io", "splent_feature_notes")  # no version
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "editable" in result.output

    def test_mixed_editable_and_versioned(self, runner, workspace):
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        make_cache_entry(workspace, "splent_io", "splent_feature_notes")  # editable
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "@v1.0.0" in result.output
        assert "editable" in result.output


# ---------------------------------------------------------------------------
# Multiple namespaces
# ---------------------------------------------------------------------------

class TestMultipleNamespaces:
    def test_different_namespaces(self, runner, workspace):
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        make_cache_entry(workspace, "drorganvidez", "custom_feature", "v0.1.0")
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "splent_io/splent_feature_auth" in result.output
        assert "drorganvidez/custom_feature" in result.output


# ---------------------------------------------------------------------------
# Non-directory entries in cache are skipped (lines 15, 18)
# ---------------------------------------------------------------------------

class TestNonDirectoryEntries:
    def test_skips_file_at_namespace_level(self, runner, workspace):
        """A file inside .splent_cache/features/ (not a dir) must be silently ignored."""
        ns = workspace / ".splent_cache" / "features" / "splent_io"
        ns.mkdir(parents=True)
        (ns / "splent_feature_auth@v1.0.0").mkdir()
        (ns / "README.txt").write_text("stray file")  # file, not dir

        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output

    def test_skips_file_at_feature_level(self, runner, workspace):
        """A file inside a namespace dir (not a feature dir) must be silently ignored."""
        ns = workspace / ".splent_cache" / "features"
        ns.mkdir(parents=True)
        (ns / "README.txt").write_text("stray file")  # file at namespace level
        (ns / "splent_io" / "splent_feature_auth@v1.0.0").mkdir(parents=True)

        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output


# ---------------------------------------------------------------------------
# Summary line
# ---------------------------------------------------------------------------

class TestSummaryLine:
    def test_shows_counts(self, runner, workspace):
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        make_cache_entry(workspace, "splent_io", "splent_feature_auth", "v2.0.0")
        make_cache_entry(workspace, "splent_io", "splent_feature_profile", "v1.0.0")
        result = runner.invoke(cache_status, [])
        assert result.exit_code == 0
        # 2 distinct features, 3 total entries
        assert "2 feature(s)" in result.output
        assert "3 total" in result.output
