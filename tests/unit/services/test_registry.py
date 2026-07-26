"""Unit tests for services/registry.py — the single remote-registry boundary.

All network is mocked at urllib.request.urlopen inside the registry module.
"""

import io
import json
import urllib.error

import pytest

from splent_cli.services import registry


def _response(payload):
    class _Resp:
        status = 200

        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp(json.dumps(payload).encode())


def _http_error(code, remaining=None):
    import email.message

    hdrs = email.message.Message()
    if remaining is not None:
        hdrs["X-RateLimit-Remaining"] = remaining
    return urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=code,
        msg="err",
        hdrs=hdrs,
        fp=io.BytesIO(b"{}"),
    )


# ── semver_sorted ───────────────────────────────────────────────────────────


class TestSemverSorted:
    def test_orders_by_semver_not_position(self):
        assert registry.semver_sorted(["v1.2.0", "v1.10.0", "v1.9.9"]) == [
            "v1.10.0",
            "v1.9.9",
            "v1.2.0",
        ]

    def test_drops_non_semver(self):
        assert registry.semver_sorted(["latest", "v2.0.0", "nightly"]) == ["v2.0.0"]

    def test_accepts_prefixless(self):
        assert registry.semver_sorted(["1.0.0", "v2.0.0"]) == ["v2.0.0", "1.0.0"]

    def test_empty(self):
        assert registry.semver_sorted([]) == []


# ── latest_semver_tag ───────────────────────────────────────────────────────


class TestLatestSemverTag:
    def test_returns_highest_semver(self, monkeypatch):
        payload = [{"name": "v1.2.0"}, {"name": "v1.10.0"}]
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.latest_semver_tag("org", "repo") == "v1.10.0"

    def test_falls_back_to_first_tag_when_no_semver(self, monkeypatch):
        payload = [{"name": "release-a"}, {"name": "release-b"}]
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.latest_semver_tag("org", "repo") == "release-a"

    def test_none_on_empty(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response([])
        )
        assert registry.latest_semver_tag("org", "repo") is None

    def test_none_on_error(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(403, remaining="0")

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.latest_semver_tag("org", "repo") is None


# ── error mapping ───────────────────────────────────────────────────────────


class TestErrorMapping:
    def test_404_is_none(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.github_json("https://api.github.com/x") is None

    def test_403_with_zero_remaining_is_rate_limited(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(403, remaining="0")

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.github_json("https://api.github.com/x")
        assert exc.value.rate_limited is True
        assert exc.value.status == 403

    def test_403_without_header_is_forbidden_not_rate_limited(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(403)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.github_json("https://api.github.com/x")
        assert exc.value.rate_limited is False
        assert exc.value.status == 403

    def test_429_is_rate_limited(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(429)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.github_json("https://api.github.com/x")
        assert exc.value.rate_limited is True


# ── list_semver_tags ────────────────────────────────────────────────────────


class TestListSemverTags:
    def test_limit(self, monkeypatch):
        payload = [{"name": f"v1.{i}.0"} for i in range(20)]
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        result = registry.list_semver_tags("org", "repo", limit=3)
        assert result == ["v1.19.0", "v1.18.0", "v1.17.0"]

    def test_quiet_on_error(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(500)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.list_semver_tags("org", "repo") == []


# ── PyPI ────────────────────────────────────────────────────────────────────


class TestPyPI:
    def test_versions_sorted_by_upload_time(self, monkeypatch):
        payload = {
            "releases": {
                "1.0.0": [{"upload_time": "2026-01-01T00:00:00"}],
                "1.1.0": [{"upload_time": "2026-06-01T00:00:00"}],
            }
        }
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.pypi_versions("pkg") == ["1.1.0", "1.0.0"]

    def test_versions_quiet_on_404(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.pypi_versions("pkg") == []
