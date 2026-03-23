"""
Tests for the cache:versions command.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.cache.cache_versions import cache_versions


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_cache(workspace, namespace, dir_name):
    path = workspace / ".splent_cache" / "features" / namespace / dir_name
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_rejects_missing_slash(self, runner, workspace):
        result = runner.invoke(cache_versions, ["auth"])
        assert result.exit_code == 1
        assert "namespace/feature_name" in result.output

    def test_accepts_slash_format(self, runner, workspace):
        result = runner.invoke(cache_versions, ["splent_io/auth"])
        # Either finds entries or warns — just shouldn't crash on format
        assert result.exit_code in (0, 1)


# ---------------------------------------------------------------------------
# Namespace not found
# ---------------------------------------------------------------------------

class TestNamespaceNotFound:
    def test_warns_when_namespace_missing(self, runner, workspace):
        result = runner.invoke(cache_versions, ["missing_ns/auth"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# No entries for feature
# ---------------------------------------------------------------------------

class TestNoEntries:
    def test_warns_when_feature_not_found(self, runner, workspace):
        # Namespace exists but feature doesn't
        (workspace / ".splent_cache" / "features" / "splent_io").mkdir(parents=True)
        result = runner.invoke(cache_versions, ["splent_io/missing_feature"])
        assert result.exit_code == 0
        assert "No cache entries" in result.output


# ---------------------------------------------------------------------------
# Versioned entries
# ---------------------------------------------------------------------------

class TestVersionedEntries:
    def test_shows_version(self, runner, workspace):
        _make_cache(workspace, "splent_io", "auth@v1.0.0")
        result = runner.invoke(cache_versions, ["splent_io/auth"])
        assert result.exit_code == 0
        assert "v1.0.0" in result.output

    def test_shows_multiple_versions(self, runner, workspace):
        _make_cache(workspace, "splent_io", "auth@v1.0.0")
        _make_cache(workspace, "splent_io", "auth@v2.0.0")
        result = runner.invoke(cache_versions, ["splent_io/auth"])
        assert "v1.0.0" in result.output
        assert "v2.0.0" in result.output


# ---------------------------------------------------------------------------
# Editable entry
# ---------------------------------------------------------------------------

class TestEditableEntry:
    def test_shows_editable_label(self, runner, workspace):
        _make_cache(workspace, "splent_io", "auth")  # no @version = editable
        result = runner.invoke(cache_versions, ["splent_io/auth"])
        assert result.exit_code == 0
        assert "editable" in result.output

    def test_shows_both_editable_and_versioned(self, runner, workspace):
        _make_cache(workspace, "splent_io", "auth")
        _make_cache(workspace, "splent_io", "auth@v1.0.0")
        result = runner.invoke(cache_versions, ["splent_io/auth"])
        assert "editable" in result.output
        assert "v1.0.0" in result.output


# ---------------------------------------------------------------------------
# Namespace normalisation (dash → underscore)
# ---------------------------------------------------------------------------

class TestNamespaceNormalisation:
    def test_dash_converted_to_underscore(self, runner, workspace):
        _make_cache(workspace, "splent_io", "auth@v1.0.0")
        # Input with hyphen should map to underscore on disk
        result = runner.invoke(cache_versions, ["splent-io/auth"])
        assert result.exit_code == 0
        assert "v1.0.0" in result.output
