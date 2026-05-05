"""
Tests for the API client helpers.
"""

from unittest.mock import patch

from splent_cli.services import api_client


class TestGetPackageByName:
    def test_get_package_by_name_preserves_namespace_slash(self):
        with patch("splent_cli.services.api_client.get") as get:
            api_client.get_package_by_name("splent-io/splent_feature_auth")

        get.assert_called_once_with(
            "/api/packages/splent-io/splent_feature_auth"
        )

    def test_get_package_by_name_quotes_spaces_but_not_slashes(self):
        with patch("splent_cli.services.api_client.get") as get:
            api_client.get_package_by_name("splent-io/feature with space")

        get.assert_called_once_with(
            "/api/packages/splent-io/feature%20with%20space"
        )


class TestHeaders:
    def test_headers_include_bearer_token_when_configured(self, monkeypatch):
        monkeypatch.setenv("SPLENT_API_TOKEN", "abc123")

        assert api_client._headers() == {"Authorization": "Bearer abc123"}


class TestBaseUrl:
    def test_base_url_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("SPLENT_API_URL", "http://api.local/")

        assert api_client._base_url() == "http://api.local"
