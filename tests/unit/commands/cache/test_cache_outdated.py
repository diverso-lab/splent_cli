"""Tests for pure helper functions in cache_outdated.py."""
import pytest

from splent_cli.commands.cache.cache_outdated import (
    _get_cache_versions,
    _get_product_features,
    _latest,
)


class TestGetCacheVersions:
    def test_finds_versioned_entries(self, tmp_path):
        ns = tmp_path / "splent_io"
        (ns / "auth@v1.0.0").mkdir(parents=True)
        (ns / "auth@v1.1.0").mkdir(parents=True)
        result = _get_cache_versions(tmp_path)
        assert "v1.0.0" in result["auth"]
        assert "v1.1.0" in result["auth"]

    def test_groups_multiple_features(self, tmp_path):
        ns = tmp_path / "splent_io"
        (ns / "auth@v1.0.0").mkdir(parents=True)
        (ns / "profile@v2.0.0").mkdir(parents=True)
        result = _get_cache_versions(tmp_path)
        assert "auth" in result
        assert "profile" in result

    def test_ignores_unversioned_entries(self, tmp_path):
        ns = tmp_path / "splent_io"
        (ns / "auth").mkdir(parents=True)
        result = _get_cache_versions(tmp_path)
        assert "auth" not in result

    def test_empty_when_cache_missing(self, tmp_path):
        result = _get_cache_versions(tmp_path / "nonexistent")
        assert dict(result) == {}

    def test_empty_when_namespace_dir_is_empty(self, tmp_path):
        (tmp_path / "splent_io").mkdir()
        result = _get_cache_versions(tmp_path)
        assert dict(result) == {}


class TestLatest:
    def test_picks_highest_semver(self):
        assert _latest(["v1.0.0", "v1.2.0", "v1.1.0"]) == "v1.2.0"

    def test_handles_v_prefix(self):
        assert _latest(["v2.0.0", "v1.9.9"]) == "v2.0.0"

    def test_single_version(self):
        assert _latest(["v1.0.0"]) == "v1.0.0"

    def test_patch_version_ordering(self):
        assert _latest(["v1.0.1", "v1.0.9", "v1.0.2"]) == "v1.0.9"

    def test_major_version_wins(self):
        assert _latest(["v1.9.9", "v2.0.0"]) == "v2.0.0"

    def test_invalid_version_loses_to_valid(self):
        result = _latest(["not-semver", "v1.0.0"])
        assert result == "v1.0.0"


class TestGetProductFeatures:
    def _make_product(self, tmp_path, name, features_list):
        product = tmp_path / name
        product.mkdir()
        entries = ", ".join(f'"{f}"' for f in features_list)
        (product / "pyproject.toml").write_text(
            f"[project.optional-dependencies]\nfeatures = [{entries}]\n"
        )
        return product

    def test_parses_versioned_features(self, tmp_path):
        self._make_product(tmp_path, "myapp", ["ns/feat_a@v1.0.0"])
        result = _get_product_features(tmp_path)
        assert "myapp" in result
        assert result["myapp"]["feat_a"] == "v1.0.0"

    def test_parses_editable_features(self, tmp_path):
        self._make_product(tmp_path, "myapp", ["ns/feat_b"])
        result = _get_product_features(tmp_path)
        assert result["myapp"]["feat_b"] is None

    def test_strips_namespace(self, tmp_path):
        self._make_product(tmp_path, "myapp", ["splent-io/auth@v2.0.0"])
        result = _get_product_features(tmp_path)
        assert "auth" in result["myapp"]

    def test_skips_dirs_without_pyproject(self, tmp_path):
        (tmp_path / "empty_app").mkdir()
        result = _get_product_features(tmp_path)
        assert "empty_app" not in result

    def test_skips_hidden_dirs(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        entries = '"ns/feat"'
        (hidden / "pyproject.toml").write_text(
            f"[project.optional-dependencies]\nfeatures = [{entries}]\n"
        )
        result = _get_product_features(tmp_path)
        assert ".hidden" not in result

    def test_skips_product_with_no_features_section(self, tmp_path):
        product = tmp_path / "myapp"
        product.mkdir()
        (product / "pyproject.toml").write_text('[project]\nname = "myapp"\n')
        result = _get_product_features(tmp_path)
        assert "myapp" not in result
