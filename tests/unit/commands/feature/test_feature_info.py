"""
Tests for feature:info.
"""

from unittest.mock import patch

from click.testing import CliRunner
import pytest

from splent_cli.commands.feature.feature_info import (
    _feature_api_name,
    feature_info,
)


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# _feature_api_name
# ---------------------------------------------------------------------------


class TestFeatureApiName:
    def test_bare_name_gets_feature_prefix(self):
        assert _feature_api_name("auth") == "splent_feature_auth"

    def test_prefixed_bare_name_is_preserved(self):
        assert (
            _feature_api_name("splent_feature_auth") == "splent_feature_auth"
        )

    def test_namespaced_name_gets_feature_prefix_on_name_only(self):
        assert (
            _feature_api_name("splent-io/auth")
            == "splent-io/splent_feature_auth"
        )

    def test_namespaced_prefixed_name_is_preserved(self):
        assert (
            _feature_api_name("splent-io/splent_feature_auth")
            == "splent-io/splent_feature_auth"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestFeatureInfoCommand:
    def test_uses_normalized_namespaced_api_name(self, runner):
        package = {
            "name": "splent_feature_auth",
            "full_name": "splent-io/splent_feature_auth",
            "contract": {"description": "Auth feature"},
        }

        with patch(
            "splent_cli.commands.feature.feature_info.get_package_by_name",
            return_value=package,
        ) as get_package:
            result = runner.invoke(feature_info, ["splent-io/auth"])

        assert result.exit_code == 0
        get_package.assert_called_once_with("splent-io/splent_feature_auth")
        assert "splent-io/splent_feature_auth" in result.output
