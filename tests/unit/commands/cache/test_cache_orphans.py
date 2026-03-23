"""
Tests for the cache:orphans command.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.cache.cache_orphans import (
    cache_orphans,
    _get_cache_entries,
    _get_all_product_refs,
)


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_cache(workspace, namespace, name, version=None):
    dir_name = f"{name}@{version}" if version else name
    path = workspace / ".splent_cache" / "features" / namespace / dir_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_product(workspace, name, features=None):
    product_dir = workspace / name
    product_dir.mkdir(parents=True, exist_ok=True)
    features_list = "\n".join(f'  "{f}",' for f in (features or []))
    (product_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\n\n'
        f'[project.optional-dependencies]\nfeatures = [\n{features_list}\n]\n'
    )
    return product_dir


# ---------------------------------------------------------------------------
# _get_cache_entries() helper
# ---------------------------------------------------------------------------

class TestGetCacheEntries:
    def test_returns_empty_for_missing_cache(self, tmp_path):
        assert _get_cache_entries(tmp_path / "missing") == []

    def test_versioned_entry(self, tmp_path):
        (tmp_path / "splent_io" / "auth@v1.0.0").mkdir(parents=True)
        entries = _get_cache_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["version"] == "v1.0.0"
        assert entries[0]["is_versioned"] is True

    def test_editable_entry(self, tmp_path):
        (tmp_path / "splent_io" / "auth").mkdir(parents=True)
        entries = _get_cache_entries(tmp_path)
        assert len(entries) == 1
        assert entries[0]["version"] is None
        assert entries[0]["is_versioned"] is False

    def test_skips_files_in_namespace_dir(self, tmp_path):
        ns_dir = tmp_path / "splent_io"
        ns_dir.mkdir()
        (ns_dir / "not_a_dir.txt").write_text("x")
        assert _get_cache_entries(tmp_path) == []


# ---------------------------------------------------------------------------
# _get_all_product_refs() helper
# ---------------------------------------------------------------------------

class TestGetAllProductRefs:
    def test_extracts_versioned_ref(self, tmp_path):
        _make_product(tmp_path, "myapp", ["splent_io/auth@v1.0.0"])
        refs = _get_all_product_refs(tmp_path)
        assert "auth@v1.0.0" in refs

    def test_extracts_bare_ref(self, tmp_path):
        _make_product(tmp_path, "myapp", ["auth"])
        refs = _get_all_product_refs(tmp_path)
        assert "auth" in refs

    def test_ignores_hidden_dirs(self, tmp_path):
        _make_product(tmp_path, ".splent_cache", ["auth@v1"])
        refs = _get_all_product_refs(tmp_path)
        assert "auth@v1" not in refs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCacheOrphansCommand:
    def test_empty_cache_shows_info(self, runner, workspace):
        result = runner.invoke(cache_orphans, [])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()

    def test_all_referenced_shows_no_orphans(self, runner, workspace):
        _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        _make_product(workspace, "test_app", ["splent_io/splent_feature_auth@v1.0.0"])

        result = runner.invoke(cache_orphans, [])
        assert result.exit_code == 0
        assert "No orphaned" in result.output

    def test_unreferenced_shows_as_orphan(self, runner, workspace):
        _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        # No product references it

        result = runner.invoke(cache_orphans, [])
        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "Orphaned" in result.output

    def test_mixed_referenced_and_orphaned(self, runner, workspace):
        _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        _make_cache(workspace, "splent_io", "splent_feature_payments", "v2.0.0")
        _make_product(workspace, "test_app", ["splent_io/splent_feature_auth@v1.0.0"])

        result = runner.invoke(cache_orphans, [])
        assert result.exit_code == 0
        assert "splent_feature_payments" in result.output
        assert "splent_feature_auth" not in result.output.split("Orphaned")[1] if "Orphaned" in result.output else True
