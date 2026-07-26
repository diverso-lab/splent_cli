"""Unit tests for services/marketplace_url.py — the registry URL resolver.

The contract under test: one registry always resolves to ONE string, because
that string is the credential-store key.
"""

import pytest

from splent_cli.services.marketplace_url import (
    DEFAULT_REGISTRY_URL,
    REGISTRY_URL_ENV,
    InvalidRegistryURL,
    normalize_registry_url,
    resolve_registry,
    resolve_registry_url,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(REGISTRY_URL_ENV, raising=False)


# ── Precedence ──────────────────────────────────────────────────────────────


class TestPrecedence:
    def test_default_is_production(self):
        assert resolve_registry_url() == "https://marketplace.splent.io"
        assert DEFAULT_REGISTRY_URL == "https://marketplace.splent.io"

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv(REGISTRY_URL_ENV, "http://localhost:5818")
        assert resolve_registry_url() == "http://localhost:5818"

    def test_option_overrides_env(self, monkeypatch):
        monkeypatch.setenv(REGISTRY_URL_ENV, "http://localhost:5818")
        url = resolve_registry_url("http://splent_marketplace_app_web:5000")
        assert url == "http://splent_marketplace_app_web:5000"

    def test_blank_option_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv(REGISTRY_URL_ENV, "http://localhost:5818")
        assert resolve_registry_url("   ") == "http://localhost:5818"

    def test_blank_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(REGISTRY_URL_ENV, "")
        assert resolve_registry_url() == DEFAULT_REGISTRY_URL

    def test_origin_is_reported(self, monkeypatch):
        assert resolve_registry().origin == "default"
        monkeypatch.setenv(REGISTRY_URL_ENV, "http://localhost:5818")
        env_target = resolve_registry()
        assert env_target.origin == "env"
        assert env_target.origin_label == REGISTRY_URL_ENV
        option_target = resolve_registry("http://localhost:1234")
        assert option_target.origin == "option"
        assert option_target.origin_label == "--registry"

    def test_env_value_is_normalised_too(self, monkeypatch):
        monkeypatch.setenv(REGISTRY_URL_ENV, "  http://localhost:5818/  ")
        assert resolve_registry_url() == "http://localhost:5818"

    def test_broken_env_names_the_variable(self, monkeypatch):
        monkeypatch.setenv(REGISTRY_URL_ENV, "ftp://example.com")
        with pytest.raises(InvalidRegistryURL) as exc:
            resolve_registry()
        assert REGISTRY_URL_ENV in str(exc.value)


# ── Normalisation ───────────────────────────────────────────────────────────


class TestNormalisation:
    def test_trailing_slash_stripped(self):
        assert (
            normalize_registry_url("https://marketplace.splent.io/")
            == "https://marketplace.splent.io"
        )

    def test_repeated_trailing_slashes_stripped(self):
        assert (
            normalize_registry_url("https://marketplace.splent.io///")
            == "https://marketplace.splent.io"
        )

    def test_one_registry_one_cache_key(self):
        """Every spelling of the same registry collapses to one string."""
        spellings = [
            "http://localhost:5818",
            "http://localhost:5818/",
            "  http://localhost:5818  ",
            "HTTP://LOCALHOST:5818",
            "localhost:5818",
        ]
        assert {normalize_registry_url(s) for s in spellings} == {
            "http://localhost:5818"
        }

    def test_host_lowercased_path_preserved(self):
        assert (
            normalize_registry_url("https://Marketplace.Splent.IO/API/Base/")
            == "https://marketplace.splent.io/API/Base"
        )

    def test_default_ports_dropped(self):
        assert (
            normalize_registry_url("https://marketplace.splent.io:443")
            == "https://marketplace.splent.io"
        )
        assert normalize_registry_url("http://example.com:80") == "http://example.com"

    def test_non_default_port_kept(self):
        assert (
            normalize_registry_url("http://splent_marketplace_app_web:5000")
            == "http://splent_marketplace_app_web:5000"
        )

    def test_query_and_fragment_dropped(self):
        assert (
            normalize_registry_url("https://marketplace.splent.io/?a=1#frag")
            == "https://marketplace.splent.io"
        )

    def test_scheme_inferred_https_for_public_host(self):
        assert (
            normalize_registry_url("marketplace.splent.io")
            == "https://marketplace.splent.io"
        )

    def test_scheme_inferred_http_for_docker_service_name(self):
        """A bare service name is only reachable over plain HTTP on the network."""
        assert (
            normalize_registry_url("splent_marketplace_app_web:5000")
            == "http://splent_marketplace_app_web:5000"
        )

    def test_scheme_inferred_http_for_localhost(self):
        assert normalize_registry_url("localhost:5818") == "http://localhost:5818"
        assert normalize_registry_url("127.0.0.1:5818") == "http://127.0.0.1:5818"


# ── Rejections ──────────────────────────────────────────────────────────────


class TestRejections:
    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_empty_is_rejected(self, value):
        with pytest.raises(InvalidRegistryURL):
            normalize_registry_url(value)

    def test_non_http_scheme_is_rejected(self):
        with pytest.raises(InvalidRegistryURL) as exc:
            normalize_registry_url("ftp://example.com")
        assert "http" in str(exc.value)

    def test_whitespace_inside_is_rejected(self):
        with pytest.raises(InvalidRegistryURL):
            normalize_registry_url("http://exa mple.com")

    def test_missing_host_is_rejected(self):
        with pytest.raises(InvalidRegistryURL):
            normalize_registry_url("http:///path")

    def test_embedded_credentials_are_rejected(self):
        """Credentials in the URL would leak into the store key and output."""
        with pytest.raises(InvalidRegistryURL) as exc:
            normalize_registry_url("https://user:secret@marketplace.splent.io")
        assert "splent login" in str(exc.value)
