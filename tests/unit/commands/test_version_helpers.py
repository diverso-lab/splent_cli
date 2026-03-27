"""Tests for pure helper functions in version.py."""
import pytest

from splent_cli.commands.version import (
    _declared_features,
    _feature_location,
    _fingerprint,
    _parse_feature_ref,
    _product_version,
    _pyproject_version,
)


class TestPyprojectVersion:
    def test_returns_version_from_valid_file(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(
            b'[project]\nversion = "1.2.3"\n'
        )
        assert _pyproject_version(str(tmp_path / "pyproject.toml")) == "1.2.3"

    def test_returns_none_when_file_missing(self, tmp_path):
        assert _pyproject_version(str(tmp_path / "nonexistent.toml")) is None

    def test_returns_none_on_malformed_toml(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_bytes(b"[invalid toml content\n")
        assert _pyproject_version(str(p)) is None

    def test_returns_none_when_version_key_absent(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_bytes(b'[project]\nname = "myapp"\n')
        assert _pyproject_version(str(p)) is None


class TestProductVersion:
    def test_reads_version_from_product_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(
            b'[project]\nversion = "2.0.0"\n'
        )
        assert _product_version(str(tmp_path)) == "2.0.0"

    def test_returns_none_when_pyproject_missing(self, tmp_path):
        assert _product_version(str(tmp_path / "missing")) is None

    def test_returns_none_on_malformed_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(b"[bad\n")
        assert _product_version(str(tmp_path)) is None


class TestDeclaredFeatures:
    def test_returns_features_list(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(
            b'[tool.splent]\n'
            b'features = ["ns/feat_a@v1", "ns/feat_b"]\n'
        )
        result = _declared_features(str(tmp_path))
        assert "ns/feat_a@v1" in result
        assert "ns/feat_b" in result

    def test_returns_empty_list_when_no_pyproject(self, tmp_path):
        assert _declared_features(str(tmp_path / "missing")) == []

    def test_returns_empty_list_on_malformed_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(b"[bad toml\n")
        assert _declared_features(str(tmp_path)) == []

    def test_returns_empty_list_when_no_features_key(self, tmp_path):
        (tmp_path / "pyproject.toml").write_bytes(b'[project]\nname = "x"\n')
        assert _declared_features(str(tmp_path)) == []


class TestFeatureLocation:
    def test_versioned_in_cache(self, tmp_path):
        cache = (
            tmp_path / ".splent_cache" / "features" / "splent_io" / "auth@v1"
        )
        cache.mkdir(parents=True)
        assert _feature_location(str(tmp_path), "splent_io/auth@v1") == "cache"

    def test_versioned_missing(self, tmp_path):
        assert (
            _feature_location(str(tmp_path), "splent_io/auth@v1") == "missing"
        )

    def test_editable_at_workspace_root(self, tmp_path):
        (tmp_path / "auth").mkdir()
        assert (
            _feature_location(str(tmp_path), "splent_io/auth") == "workspace"
        )

    def test_editable_in_cache_fallback(self, tmp_path):
        cache = tmp_path / ".splent_cache" / "features" / "splent_io" / "auth"
        cache.mkdir(parents=True)
        assert _feature_location(str(tmp_path), "splent_io/auth") == "cache"

    def test_editable_missing(self, tmp_path):
        assert (
            _feature_location(str(tmp_path), "splent_io/auth") == "missing"
        )

    def test_no_namespace_prefix_defaults(self, tmp_path):
        (tmp_path / "auth").mkdir()
        assert _feature_location(str(tmp_path), "auth") == "workspace"


class TestFingerprint:
    def test_deterministic(self):
        assert _fingerprint(["1.0", "2.0", "py3"]) == _fingerprint(
            ["1.0", "2.0", "py3"]
        )

    def test_different_inputs_differ(self):
        assert _fingerprint(["a"]) != _fingerprint(["b"])

    def test_returns_8_chars(self):
        assert len(_fingerprint(["a", "b"])) == 8

    def test_empty_list(self):
        result = _fingerprint([])
        assert len(result) == 8


class TestParseFeatureRef:
    def test_versioned_with_namespace(self):
        assert _parse_feature_ref("splent_io/auth@v1") == ("auth", "v1")

    def test_editable_with_namespace(self):
        assert _parse_feature_ref("splent_io/auth") == ("auth", None)

    def test_no_namespace(self):
        assert _parse_feature_ref("auth@v2") == ("auth", "v2")

    def test_name_only(self):
        name, ver = _parse_feature_ref("myfeature")
        assert name == "myfeature"
        assert ver is None
