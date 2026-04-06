"""
Tests for env-aware feature reading (features + features_dev + features_prod).
"""
import pytest
from splent_cli.utils.feature_utils import read_features_from_data, write_features_to_data


@pytest.fixture
def pyproject_data():
    return {
        "tool": {
            "splent": {
                "features": [
                    "splent-io/splent_feature_auth@v1.2.2",
                    "splent-io/splent_feature_public@v1.2.2",
                ],
                "features_dev": [
                    "splent-io/splent_feature_debug@v1.0.0",
                    "splent-io/splent_feature_faker@v1.0.0",
                ],
                "features_prod": [
                    "splent-io/splent_feature_monitoring@v1.0.0",
                ],
            }
        }
    }


class TestReadFeaturesFromData:

    def test_no_env_returns_base_only(self, pyproject_data):
        result = read_features_from_data(pyproject_data)
        assert len(result) == 2
        assert "splent-io/splent_feature_auth@v1.2.2" in result
        assert "splent-io/splent_feature_debug@v1.0.0" not in result

    def test_env_none_returns_base_only(self, pyproject_data):
        result = read_features_from_data(pyproject_data, env=None)
        assert len(result) == 2

    def test_dev_merges_base_and_dev(self, pyproject_data):
        result = read_features_from_data(pyproject_data, env="dev")
        assert len(result) == 4
        assert "splent-io/splent_feature_auth@v1.2.2" in result
        assert "splent-io/splent_feature_debug@v1.0.0" in result
        assert "splent-io/splent_feature_faker@v1.0.0" in result
        assert "splent-io/splent_feature_monitoring@v1.0.0" not in result

    def test_prod_merges_base_and_prod(self, pyproject_data):
        result = read_features_from_data(pyproject_data, env="prod")
        assert len(result) == 3
        assert "splent-io/splent_feature_auth@v1.2.2" in result
        assert "splent-io/splent_feature_monitoring@v1.0.0" in result
        assert "splent-io/splent_feature_debug@v1.0.0" not in result

    def test_base_features_come_first(self, pyproject_data):
        result = read_features_from_data(pyproject_data, env="dev")
        assert result[0] == "splent-io/splent_feature_auth@v1.2.2"
        assert result[1] == "splent-io/splent_feature_public@v1.2.2"

    def test_deduplicates_overlapping_features(self):
        data = {
            "tool": {"splent": {
                "features": ["splent-io/feat_a@v1.0.0"],
                "features_dev": ["splent-io/feat_a@v1.0.0", "splent-io/feat_b@v1.0.0"],
            }}
        }
        result = read_features_from_data(data, env="dev")
        assert len(result) == 2
        assert result.count("splent-io/feat_a@v1.0.0") == 1

    def test_missing_env_section_returns_base_only(self):
        data = {"tool": {"splent": {"features": ["feat_a"]}}}
        result = read_features_from_data(data, env="dev")
        assert result == ["feat_a"]

    def test_empty_base_with_env_features(self):
        data = {"tool": {"splent": {
            "features": [],
            "features_dev": ["splent-io/feat_debug@v1.0.0"],
        }}}
        result = read_features_from_data(data, env="dev")
        assert result == ["splent-io/feat_debug@v1.0.0"]

    def test_legacy_fallback_without_env(self):
        data = {"project": {"optional-dependencies": {"features": ["legacy_feat"]}}}
        result = read_features_from_data(data)
        assert result == ["legacy_feat"]

    def test_unknown_env_returns_base_only(self, pyproject_data):
        result = read_features_from_data(pyproject_data, env="staging")
        assert len(result) == 2


class TestWriteFeaturesToData:

    def test_writes_to_tool_splent(self):
        data = {"tool": {"splent": {}}}
        write_features_to_data(data, ["feat_a"], key="features")
        assert data["tool"]["splent"]["features"] == ["feat_a"]

    def test_writes_dev_features(self):
        data = {"tool": {"splent": {}}}
        write_features_to_data(data, ["feat_debug"], key="features_dev")
        assert data["tool"]["splent"]["features_dev"] == ["feat_debug"]

    def test_writes_prod_features(self):
        data = {"tool": {"splent": {}}}
        write_features_to_data(data, ["feat_monitor"], key="features_prod")
        assert data["tool"]["splent"]["features_prod"] == ["feat_monitor"]

    def test_removes_legacy_on_base_write(self):
        data = {
            "project": {"optional-dependencies": {"features": ["old"]}},
            "tool": {"splent": {}},
        }
        write_features_to_data(data, ["new"], key="features")
        assert "features" not in data["project"]["optional-dependencies"]
        assert data["tool"]["splent"]["features"] == ["new"]
