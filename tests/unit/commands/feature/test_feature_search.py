"""
Tests for feature:search.
"""

from unittest.mock import patch

from click.testing import CliRunner
import pytest

from splent_cli.commands.feature.feature_search import (
    _contract_description,
    _load_packages,
    _updated_at,
    feature_search,
)
from splent_cli.services.api_client import SplentAPIAuthError, SplentAPIError


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def logged_in(monkeypatch):
    monkeypatch.setattr(
        "splent_cli.commands.feature.feature_search.marketplace.require_marketplace_login",
        lambda: True,
    )


def _package(name, description="", updated_at="2026-05-04T10:00:00Z"):
    return {
        "name": name,
        "contract": {"description": description},
        "metadata": {"updated_at": updated_at},
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_contract_description_uses_contract_description(self):
        package = {"contract": {"description": "Auth feature"}}

        assert _contract_description(package) == "Auth feature"

    def test_contract_description_returns_empty_when_missing(self):
        assert _contract_description({}) == ""

    def test_updated_at_prefers_metadata_and_strips_time(self):
        package = {
            "updated_at": "2026-04-01T12:00:00Z",
            "metadata": {"updated_at": "2026-05-04T10:00:00Z"},
        }

        assert _updated_at(package) == "2026-05-04"

    def test_updated_at_falls_back_to_top_level_value(self):
        assert _updated_at({"updated_at": "2026-04-01"}) == "2026-04-01"

    def test_updated_at_returns_dash_when_missing(self):
        assert _updated_at({}) == "-"


# ---------------------------------------------------------------------------
# _load_packages
# ---------------------------------------------------------------------------


class TestLoadPackages:
    def test_returns_only_dict_items_from_api_list(self):
        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            return_value=[_package("splent_feature_auth"), "bad", None],
        ):
            packages = _load_packages()

        assert packages == [_package("splent_feature_auth")]

    def test_raises_for_unexpected_api_response(self):
        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            return_value={"items": []},
        ):
            with pytest.raises(SplentAPIError, match="Unexpected"):
                _load_packages()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestFeatureSearchCommand:
    def test_filters_to_splent_feature_packages_by_default(self, runner):
        packages = [
            _package("splent_feature_auth", "Auth feature"),
            _package("regular_package", "Regular package"),
        ]

        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            return_value=packages,
        ):
            result = runner.invoke(feature_search)

        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "Auth feature" in result.output
        assert "regular_package" not in result.output

    def test_all_includes_non_feature_packages(self, runner):
        packages = [
            _package("splent_feature_auth", "Auth feature"),
            _package("regular_package", "Regular package"),
        ]

        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            return_value=packages,
        ):
            result = runner.invoke(feature_search, ["--all"])

        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "regular_package" in result.output

    def test_query_filters_case_insensitively(self, runner):
        packages = [
            _package("splent_feature_auth", "Auth feature"),
            _package("splent_feature_billing", "Billing feature"),
        ]

        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            return_value=packages,
        ):
            result = runner.invoke(feature_search, ["AUTH"])

        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "splent_feature_billing" not in result.output

    def test_outputs_empty_message_when_no_packages_match(self, runner):
        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            return_value=[_package("splent_feature_auth")],
        ):
            result = runner.invoke(feature_search, ["billing"])

        assert result.exit_code == 0
        assert "No packages found matching 'billing'" in result.output

    def test_exits_cleanly_when_api_client_fails(self, runner):
        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            side_effect=SplentAPIError("Could not connect"),
        ):
            result = runner.invoke(feature_search)

        assert result.exit_code == 1
        assert "Could not connect" in result.output
        assert "Check SPLENT_API_URL" in result.output

    def test_exits_with_login_message_when_auth_fails(self, runner):
        with patch(
            "splent_cli.commands.feature.feature_search.get_packages",
            side_effect=SplentAPIAuthError(
                "Marketplace login required. Run: splent marketplace:login"
            ),
        ):
            result = runner.invoke(feature_search, ["--all"])

        assert result.exit_code == 1
        assert "Marketplace login required" in result.output
        assert "splent marketplace:login" in result.output
        assert "Check SPLENT_API_URL" not in result.output

    def test_requires_marketplace_login_before_loading_packages(
        self, runner, monkeypatch
    ):
        monkeypatch.setattr(
            "splent_cli.commands.feature.feature_search.marketplace.require_marketplace_login",
            lambda: (_ for _ in ()).throw(
                SplentAPIAuthError(
                    "Marketplace login required. Run: splent marketplace:login"
                )
            ),
        )

        with patch(
            "splent_cli.commands.feature.feature_search.get_packages"
        ) as get_packages:
            result = runner.invoke(feature_search, ["--all"])

        assert result.exit_code == 1
        assert "Marketplace login required" in result.output
        get_packages.assert_not_called()
