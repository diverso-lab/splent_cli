"""
Tests for feature:info.
"""

from unittest.mock import patch

from click.testing import CliRunner
import pytest

from splent_cli.commands.feature.feature_info import (
    _feature_api_candidates,
    _feature_api_name,
    feature_info,
)
from splent_cli.services.api_client import SplentAPIError


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

    def test_candidates_include_original_and_normalized_namespaced_name(self):
        assert _feature_api_candidates("splent-io/demosito") == [
            "splent-io/demosito",
            "splent-io/splent_feature_demosito",
        ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestFeatureInfoCommand:
    def test_uses_original_namespaced_api_name_first(self, runner):
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
        get_package.assert_called_once_with("splent-io/auth")
        assert "splent-io/splent_feature_auth" in result.output

    def test_uses_original_namespaced_name_when_found(self, runner):
        package = {
            "name": "demosito",
            "full_name": "splent-io/demosito",
            "contract": {"description": "Demosito feature"},
        }

        with patch(
            "splent_cli.commands.feature.feature_info.get_package_by_name",
            return_value=package,
        ) as get_package:
            result = runner.invoke(feature_info, ["splent-io/demosito"])

        assert result.exit_code == 0
        get_package.assert_called_once_with("splent-io/demosito")
        assert "splent-io/demosito" in result.output

    def test_falls_back_to_normalized_namespaced_name_after_500(self, runner):
        package = {
            "name": "splent_feature_auth",
            "full_name": "splent-io/splent_feature_auth",
            "contract": {"description": "Auth feature"},
        }

        with patch(
            "splent_cli.commands.feature.feature_info.get_package_by_name",
            side_effect=[SplentAPIError("SPLENT API returned HTTP 500."), package],
        ) as get_package:
            result = runner.invoke(feature_info, ["splent-io/auth"])

        assert result.exit_code == 0
        assert get_package.call_args_list[0].args == ("splent-io/auth",)
        assert get_package.call_args_list[1].args == (
            "splent-io/splent_feature_auth",
        )
        assert "splent-io/splent_feature_auth" in result.output
