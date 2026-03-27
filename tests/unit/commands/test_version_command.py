"""Tests for the version command and its pure helpers."""
import json
import pytest
from click.testing import CliRunner

from splent_cli.commands.version import (
    _feature_location,
    _fingerprint,
    _parse_feature_ref,
    version,
)


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_returns_8_char_hex(self):
        result = _fingerprint(["a", "b", "c"])
        assert len(result) == 8
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_inputs_same_output(self):
        assert _fingerprint(["x", "y"]) == _fingerprint(["x", "y"])

    def test_different_inputs_different_output(self):
        assert _fingerprint(["a"]) != _fingerprint(["b"])

    def test_order_matters(self):
        assert _fingerprint(["a", "b"]) != _fingerprint(["b", "a"])

    def test_empty_list(self):
        result = _fingerprint([])
        assert len(result) == 8


# ---------------------------------------------------------------------------
# _parse_feature_ref
# ---------------------------------------------------------------------------

class TestParseFeatureRef:
    def test_namespaced_versioned(self):
        name, ver = _parse_feature_ref("splent_io/auth@v1.2.3")
        assert name == "auth"
        assert ver == "v1.2.3"

    def test_namespaced_no_version(self):
        name, ver = _parse_feature_ref("splent_io/auth")
        assert name == "auth"
        assert ver is None

    def test_no_namespace_versioned(self):
        name, ver = _parse_feature_ref("auth@v2.0.0")
        assert name == "auth"
        assert ver == "v2.0.0"

    def test_no_namespace_no_version(self):
        name, ver = _parse_feature_ref("auth")
        assert name == "auth"
        assert ver is None


# ---------------------------------------------------------------------------
# _feature_location
# ---------------------------------------------------------------------------

class TestFeatureLocation:
    def test_returns_cache_when_versioned_dir_exists(self, tmp_path):
        cache_dir = tmp_path / ".splent_cache" / "features" / "splent_io" / "auth@v1.0.0"
        cache_dir.mkdir(parents=True)
        result = _feature_location(str(tmp_path), "splent_io/auth@v1.0.0")
        assert result == "cache"

    def test_returns_missing_when_versioned_dir_absent(self, tmp_path):
        result = _feature_location(str(tmp_path), "splent_io/auth@v1.0.0")
        assert result == "missing"

    def test_returns_workspace_when_editable_at_root(self, tmp_path):
        (tmp_path / "auth").mkdir()
        result = _feature_location(str(tmp_path), "splent_io/auth")
        assert result == "workspace"

    def test_returns_cache_when_editable_in_cache(self, tmp_path):
        cache_dir = tmp_path / ".splent_cache" / "features" / "splent_io" / "auth"
        cache_dir.mkdir(parents=True)
        result = _feature_location(str(tmp_path), "splent_io/auth")
        assert result == "cache"

    def test_returns_missing_when_editable_not_found(self, tmp_path):
        result = _feature_location(str(tmp_path), "splent_io/auth")
        assert result == "missing"

    def test_handles_dashed_namespace(self, tmp_path):
        cache_dir = tmp_path / ".splent_cache" / "features" / "splent_io" / "auth@v1.0.0"
        cache_dir.mkdir(parents=True)
        result = _feature_location(str(tmp_path), "splent-io/auth@v1.0.0")
        assert result == "cache"

    def test_no_namespace_defaults_to_splent_io(self, tmp_path):
        cache_dir = tmp_path / ".splent_cache" / "features" / "splent_io" / "auth@v1.0.0"
        cache_dir.mkdir(parents=True)
        result = _feature_location(str(tmp_path), "auth@v1.0.0")
        assert result == "cache"


# ---------------------------------------------------------------------------
# version command
# ---------------------------------------------------------------------------

class TestVersionCommand:
    def test_shows_cli_label(self, runner, workspace):
        result = runner.invoke(version)
        assert "CLI" in result.output

    def test_shows_python_label(self, runner, workspace):
        result = runner.invoke(version)
        assert "Python" in result.output

    def test_shows_fingerprint(self, runner, workspace):
        result = runner.invoke(version)
        assert "Fingerprint" in result.output

    def test_shows_product_not_selected_when_no_app(self, runner, workspace):
        result = runner.invoke(version)
        assert "not selected" in result.output

    def test_shows_product_name_when_app_set(self, runner, product_workspace):
        result = runner.invoke(version)
        assert "test_app" in result.output

    def test_json_output_is_valid(self, runner, workspace):
        result = runner.invoke(version, ["--json"])
        payload = json.loads(result.output)
        assert "cli" in payload
        assert "python" in payload
        assert "fingerprint" in payload

    def test_json_product_is_none_without_app(self, runner, workspace):
        result = runner.invoke(version, ["--json"])
        payload = json.loads(result.output)
        assert payload["product"] is None

    def test_json_product_has_name_when_app_set(self, runner, product_workspace):
        result = runner.invoke(version, ["--json"])
        payload = json.loads(result.output)
        assert payload["product"]["name"] == "test_app"

    def test_json_features_empty_when_none_declared(self, runner, product_workspace):
        result = runner.invoke(version, ["--json"])
        payload = json.loads(result.output)
        assert payload["features"] == []

    def test_json_features_listed_when_declared(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\nfeatures = ["splent_io/auth@v1.0.0"]\n'
        )
        result = runner.invoke(version, ["--json"])
        payload = json.loads(result.output)
        assert len(payload["features"]) == 1
        assert payload["features"][0]["name"] == "auth"
        assert payload["features"][0]["version"] == "v1.0.0"

    def test_json_feature_location_missing_when_not_cached(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\nfeatures = ["splent_io/auth@v1.0.0"]\n'
        )
        result = runner.invoke(version, ["--json"])
        payload = json.loads(result.output)
        assert payload["features"][0]["location"] == "missing"

    def test_json_compatible_field_is_boolean(self, runner, workspace):
        result = runner.invoke(version, ["--json"])
        payload = json.loads(result.output)
        assert isinstance(payload["compatible"], bool)
