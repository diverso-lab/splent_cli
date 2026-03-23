"""
Tests for the feature:status command.
"""

import json
import pytest
from click.testing import CliRunner
from unittest.mock import patch

from splent_cli.commands.feature.feature_status import feature_status
from splent_cli.utils.manifest import set_feature_state, feature_key


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _write_pyproject(product_path, features: list[str]):
    import tomli_w
    data = {
        "project": {
            "name": "test_product",
            "optional-dependencies": {"features": features},
        }
    }
    with open(product_path / "pyproject.toml", "wb") as f:
        tomli_w.dump(data, f)


class TestFeatureStatusWithManifest:
    def test_shows_product_name(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        key = feature_key("splent_io", "splent_feature_auth", "v1.1.1")
        set_feature_state(
            str(product_path), "test_product", key, "declared",
            namespace="splent_io", name="splent_feature_auth", version="v1.1.1",
            mode="pinned",
        )
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, [])
        assert result.exit_code == 0
        assert "test_product" in result.output

    def test_shows_feature_name(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        key = feature_key("splent_io", "splent_feature_auth", "v1.1.1")
        set_feature_state(
            str(product_path), "test_product", key, "active",
            namespace="splent_io", name="splent_feature_auth", version="v1.1.1",
            mode="pinned",
        )
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, [])
        assert "splent_feature_auth" in result.output

    def test_shows_state(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        key = feature_key("splent_io", "splent_feature_redis", "v1.1.0")
        set_feature_state(
            str(product_path), "test_product", key, "installed",
            namespace="splent_io", name="splent_feature_redis", version="v1.1.0",
            mode="pinned",
        )
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, [])
        assert "installed" in result.output

    def test_json_flag_outputs_valid_json(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        key = feature_key("splent_io", "my_feature")
        set_feature_state(
            str(product_path), "test_product", key, "declared",
            namespace="splent_io", name="my_feature",
        )
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, ["--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "features" in data

    def test_empty_manifest_shows_no_features_message(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        # Write empty manifest
        (product_path / "splent.manifest.json").write_text(
            '{"schema_version": "1", "features": {}, "product": "test_product"}'
        )
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, [])
        assert "No features" in result.output


class TestFeatureStatusFallback:
    def test_warns_when_no_manifest(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        _write_pyproject(product_path, ["splent_feature_auth@v1.1.1"])
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, [])
        assert result.exit_code == 0
        assert "splent.manifest.json" in result.output

    def test_fallback_lists_pyproject_features(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        _write_pyproject(
            product_path, ["splent_feature_auth@v1.1.1", "splent_feature_public@v1.1.0"]
        )
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, [])
        assert "splent_feature_auth" in result.output
        assert "splent_feature_public" in result.output

    def test_fallback_shows_declared_state(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        _write_pyproject(product_path, ["my_feature"])
        with patch("splent_cli.commands.feature.feature_status.context.require_app",
                   return_value="test_product"), \
             patch("splent_cli.commands.feature.feature_status.context.workspace",
                   return_value=workspace):
            result = runner.invoke(feature_status, [])
        assert "declared" in result.output
