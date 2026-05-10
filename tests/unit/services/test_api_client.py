"""
Tests for the API client helpers.
"""

from unittest.mock import patch

import pytest
import requests

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
        monkeypatch.setenv("SPLENT_MARKETPLACE_AUTH", "true")

        assert api_client._headers() == {"Authorization": "Bearer abc123"}

    def test_headers_omit_empty_quoted_token(self, monkeypatch):
        monkeypatch.setenv("SPLENT_API_TOKEN", '""')
        monkeypatch.setenv("SPLENT_MARKETPLACE_AUTH", "true")

        assert api_client._headers() == {}

    def test_headers_omit_token_when_logged_out(self, monkeypatch):
        monkeypatch.setenv("SPLENT_API_TOKEN", "abc123")
        monkeypatch.setenv("SPLENT_MARKETPLACE_AUTH", "false")

        assert api_client._headers() == {}


class TestBaseUrl:
    def test_base_url_strips_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("SPLENT_API_URL", "http://api.local/")

        assert api_client._base_url() == "http://api.local"


class TestRequestErrors:
    def test_request_raises_auth_error_for_401(self):
        response = requests.Response()
        response.status_code = 401
        error = requests.exceptions.HTTPError(response=response)

        with (
            patch("splent_cli.services.api_client.requests.request") as request,
            pytest.raises(api_client.SplentAPIAuthError, match="login required"),
        ):
            request.return_value.raise_for_status.side_effect = error
            api_client.get("/api/packages")
