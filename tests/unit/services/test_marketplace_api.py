"""Unit tests for services/marketplace_api.py — the marketplace boundary.

All network is mocked at ``requests.request`` inside the module, mirroring how
tests/unit/services/test_registry.py mocks urlopen for the GitHub boundary.
"""

import json

import pytest
import requests

from splent_cli.services import marketplace_api
from splent_cli.services.marketplace_api import (
    MarketplaceClient,
    MarketplaceError,
)

BASE = "https://marketplace.splent.io"


class _Response:
    def __init__(self, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        if headers is not None:
            self.headers = headers
        if text is not None:
            self.text = text
        elif payload is None:
            self.text = ""
        else:
            self.text = json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Recorder:
    """Captures the outgoing request and replays a canned response."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    @property
    def last(self):
        return self.calls[-1]


def _install(monkeypatch, *responses) -> _Recorder:
    recorder = _Recorder(*responses)
    monkeypatch.setattr(marketplace_api.requests, "request", recorder)
    return recorder


# ── Construction ────────────────────────────────────────────────────────────


class TestConstruction:
    def test_base_url_is_normalised(self):
        client = MarketplaceClient("https://marketplace.splent.io/")
        assert client.base_url == BASE

    def test_blank_token_means_no_token(self):
        assert MarketplaceClient(BASE, token="   ").token is None

    def test_authenticated_call_without_token_never_hits_the_network(self, monkeypatch):
        recorder = _install(monkeypatch, _Response(200, {}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).whoami()
        assert exc.value.code == marketplace_api.CODE_UNAUTHENTICATED
        assert recorder.calls == []


# ── login ───────────────────────────────────────────────────────────────────


class TestLogin:
    def test_posts_the_documented_body(self, monkeypatch):
        recorder = _install(
            monkeypatch,
            _Response(
                201,
                {
                    "token": "tok-123",
                    "expires_at": "2026-10-24T10:00:00+00:00",
                    "scopes": ["spl:publish"],
                },
            ),
        )
        result = MarketplaceClient(BASE).login(
            "dev@example.com", "s3cret", "splent-cli@laptop", 30
        )

        call = recorder.last
        assert call["method"] == "POST"
        assert call["url"] == f"{BASE}/api/v1/auth/tokens"
        assert call["json"] == {
            "email": "dev@example.com",
            "password": "s3cret",
            "name": "splent-cli@laptop",
            "ttl_days": 30,
        }
        assert "Authorization" not in call["headers"]
        assert result["token"] == "tok-123"
        assert result["scopes"] == ["spl:publish"]
        assert result["expires_at"] == "2026-10-24T10:00:00+00:00"
        assert result["identity"] == "dev@example.com"
        assert result["token_name"] == "splent-cli@laptop"

    def test_default_ttl_is_used(self, monkeypatch):
        recorder = _install(monkeypatch, _Response(201, {"token": "t"}))
        MarketplaceClient(BASE).login("dev@example.com", "pw", "label")
        assert recorder.last["json"]["ttl_days"] == marketplace_api.DEFAULT_TTL_DAYS

    def test_extra_fields_are_tolerated(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(
                201,
                {"token": "t", "brand_new_field": 1, "user": {"email": "a@b.c"}},
            ),
        )
        result = MarketplaceClient(BASE).login("a@b.c", "pw", "label")
        assert result["token"] == "t"
        assert result["raw"]["brand_new_field"] == 1

    def test_missing_token_field_is_a_clear_error(self, monkeypatch):
        _install(monkeypatch, _Response(201, {"expires_at": "soon"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).login("a@b.c", "pw", "label")
        assert exc.value.code == marketplace_api.CODE_BAD_RESPONSE
        assert "no token" in str(exc.value)

    def test_wrong_password_is_invalid_credentials(self, monkeypatch):
        _install(monkeypatch, _Response(401, {"error": "Bad e-mail or password"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).login("a@b.c", "nope", "label")
        assert exc.value.code == marketplace_api.CODE_INVALID_CREDENTIALS
        assert exc.value.status == 401

    def test_inactive_account_by_server_code(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(403, {"code": "account_inactive", "error": "not активен"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).login("a@b.c", "pw", "label")
        assert exc.value.code == marketplace_api.CODE_ACCOUNT_INACTIVE

    def test_inactive_account_by_message(self, monkeypatch):
        """The contract may not carry a code yet, the wording still classifies."""
        _install(
            monkeypatch,
            _Response(403, {"error": "This account is not activated yet"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).login("a@b.c", "pw", "label")
        assert exc.value.code == marketplace_api.CODE_ACCOUNT_INACTIVE

    def test_password_never_appears_in_the_error(self, monkeypatch):
        _install(monkeypatch, _Response(401, {"error": "nope"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).login("a@b.c", "sup3rs3cret", "label")
        assert "sup3rs3cret" not in str(exc.value)


# ── whoami ──────────────────────────────────────────────────────────────────


class TestWhoami:
    def test_sends_bearer_and_parses_flat_body(self, monkeypatch):
        recorder = _install(
            monkeypatch,
            _Response(
                200,
                {
                    "identity": "dev@example.com",
                    "scopes": ["spl:read", "spl:publish"],
                    "token_name": "ci",
                    "expires_at": "2026-10-24T10:00:00+00:00",
                },
            ),
        )
        result = MarketplaceClient(BASE, token="tok").whoami()

        call = recorder.last
        assert call["method"] == "GET"
        assert call["url"] == f"{BASE}/api/v1/auth/whoami"
        assert call["headers"]["Authorization"] == "Bearer tok"
        assert result["identity"] == "dev@example.com"
        assert result["scopes"] == ["spl:read", "spl:publish"]
        assert result["token_name"] == "ci"

    def test_parses_nested_user_and_token_objects(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(
                200,
                {
                    "user": {"email": "dev@example.com"},
                    "token": {
                        "name": "laptop",
                        "scopes": "spl:publish spl:read",
                        "expires_at": "2026-12-01T00:00:00+00:00",
                    },
                },
            ),
        )
        result = MarketplaceClient(BASE, token="tok").whoami()
        assert result["identity"] == "dev@example.com"
        assert result["token_name"] == "laptop"
        assert result["scopes"] == ["spl:publish", "spl:read"]
        assert result["expires_at"] == "2026-12-01T00:00:00+00:00"

    def test_missing_identity_is_a_clear_error(self, monkeypatch):
        _install(monkeypatch, _Response(200, {"scopes": ["spl:read"]}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_BAD_RESPONSE
        assert "identity" in str(exc.value)

    def test_non_object_body_is_a_clear_error(self, monkeypatch):
        _install(monkeypatch, _Response(200, ["nope"]))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_BAD_RESPONSE

    def test_rejected_token_is_dead(self, monkeypatch):
        _install(monkeypatch, _Response(401, {"error": "invalid token"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_UNAUTHENTICATED
        assert exc.value.dead_token is True

    def test_expired_token_is_classified_and_dead(self, monkeypatch):
        _install(monkeypatch, _Response(401, {"error": "token has expired"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_TOKEN_EXPIRED
        assert exc.value.dead_token is True

    def test_revoked_token_is_classified_and_dead(self, monkeypatch):
        _install(monkeypatch, _Response(401, {"code": "token_revoked"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_TOKEN_REVOKED
        assert exc.value.dead_token is True

    def test_inactive_account_is_not_a_dead_token(self, monkeypatch):
        """The credential is fine, the account simply is not activated."""
        _install(monkeypatch, _Response(403, {"code": "inactive"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_ACCOUNT_INACTIVE
        assert exc.value.dead_token is False

    def test_token_is_never_in_the_error_message(self, monkeypatch):
        _install(monkeypatch, _Response(500, text="boom"))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="super-secret-token").whoami()
        assert "super-secret-token" not in str(exc.value)


# ── Transport failures ──────────────────────────────────────────────────────


class TestTransport:
    def test_connection_error_is_unreachable(self, monkeypatch):
        _install(monkeypatch, requests.ConnectionError("name not resolved"))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_UNREACHABLE
        assert exc.value.unreachable is True
        assert exc.value.status is None
        assert BASE in str(exc.value)

    def test_connection_error_condenses_the_cause(self, monkeypatch):
        """urllib3 retry noise is useless to a developer, the phrase is not."""
        noisy = requests.ConnectionError(
            "HTTPConnectionPool(host='localhost', port=9999): Max retries "
            "exceeded with url: /api/v1/auth/whoami (Caused by "
            'NewConnectionError("...: [Errno 111] Connection refused"))'
        )
        _install(monkeypatch, noisy)
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.reason == "Connection refused"
        assert "Max retries" not in str(exc.value)

    def test_dns_failure_condenses_the_cause(self, monkeypatch):
        _install(
            monkeypatch,
            requests.ConnectionError(
                "Failed to resolve 'splent_marketplace_app_web' "
                "(Name or service not known)"
            ),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.reason == "Name or service not known"

    def test_timeout_is_unreachable(self, monkeypatch):
        _install(monkeypatch, requests.Timeout())
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_UNREACHABLE

    def test_server_error_is_classified(self, monkeypatch):
        _install(monkeypatch, _Response(503, text="upstream down"))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_SERVER_ERROR
        assert exc.value.status == 503

    def test_unknown_status_falls_back_to_unexpected(self, monkeypatch):
        _install(monkeypatch, _Response(418, {"error": "teapot"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_UNEXPECTED
        assert exc.value.status == 418

    def test_empty_error_body_still_produces_a_message(self, monkeypatch):
        _install(monkeypatch, _Response(500, text=""))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert "HTTP 500" in str(exc.value)

    def test_nested_error_object_is_understood(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(403, {"error": {"code": "insufficient_scope", "message": "no"}}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_FORBIDDEN
        assert exc.value.message == "no"


# ── revoke ──────────────────────────────────────────────────────────────────


class TestRevoke:
    def test_deletes_the_current_token(self, monkeypatch):
        recorder = _install(monkeypatch, _Response(204))
        assert MarketplaceClient(BASE, token="tok").revoke_current() is True
        call = recorder.last
        assert call["method"] == "DELETE"
        assert call["url"] == f"{BASE}/api/v1/auth/tokens/current"
        assert call["headers"]["Authorization"] == "Bearer tok"

    def test_already_dead_token_raises(self, monkeypatch):
        _install(monkeypatch, _Response(401, {"code": "token_revoked"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").revoke_current()
        assert exc.value.dead_token is True


# ── SPLs ────────────────────────────────────────────────────────────────────


class TestSpls:
    def test_list_accepts_a_bare_array(self, monkeypatch):
        recorder = _install(monkeypatch, _Response(200, [{"name": "cms_spl"}]))
        assert MarketplaceClient(BASE).list_spls() == [{"name": "cms_spl"}]
        assert recorder.last["url"] == f"{BASE}/api/v1/spls"
        assert "Authorization" not in recorder.last["headers"]

    def test_list_accepts_a_wrapped_array(self, monkeypatch):
        _install(monkeypatch, _Response(200, {"spls": [{"name": "cms_spl"}]}))
        assert MarketplaceClient(BASE).list_spls() == [{"name": "cms_spl"}]

    def test_list_rejects_an_unusable_shape(self, monkeypatch):
        _install(monkeypatch, _Response(200, {"unexpected": "shape"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).list_spls()
        assert exc.value.code == marketplace_api.CODE_BAD_RESPONSE

    def test_get_returns_the_spl_with_its_uvl_block(self, monkeypatch):
        payload = {
            "name": "cms_spl",
            "description": "content SPL",
            "uvl": {"mirror": "uvlhub.io", "doi": "10.x/y", "file": "cms_spl.uvl"},
            "future_field": True,
        }
        recorder = _install(monkeypatch, _Response(200, payload))
        result = MarketplaceClient(BASE).get_spl("cms_spl")
        assert recorder.last["url"] == f"{BASE}/api/v1/spls/cms_spl"
        assert result["uvl"]["doi"] == "10.x/y"
        assert result["future_field"] is True

    def test_get_unwraps_a_nested_spl(self, monkeypatch):
        _install(monkeypatch, _Response(200, {"spl": {"name": "cms_spl"}}))
        assert MarketplaceClient(BASE).get_spl("cms_spl")["name"] == "cms_spl"

    def test_get_missing_name_is_a_clear_error(self, monkeypatch):
        _install(monkeypatch, _Response(200, {"description": "no name here"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).get_spl("cms_spl")
        assert exc.value.code == marketplace_api.CODE_BAD_RESPONSE
        assert "name" in str(exc.value)

    def test_get_unknown_spl_is_not_found(self, monkeypatch):
        _install(monkeypatch, _Response(404, {"error": "no such SPL"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).get_spl("ghost")
        assert exc.value.code == marketplace_api.CODE_NOT_FOUND

    def test_publish_uploads_the_uvl_as_multipart(self, monkeypatch):
        recorder = _install(monkeypatch, _Response(201, {"version": 3}))
        result = MarketplaceClient(BASE, token="tok").publish_spl(
            "cms_spl", b"features\n    Root", "cms_spl.uvl"
        )

        call = recorder.last
        assert call["method"] == "POST"
        assert call["url"] == f"{BASE}/api/v1/spls/cms_spl/releases"
        assert call["headers"]["Authorization"] == "Bearer tok"
        filename, content, content_type = call["files"][marketplace_api.UVL_FIELD_NAME]
        assert filename == "cms_spl.uvl"
        assert content == b"features\n    Root"
        assert content_type == "text/plain"
        assert call["timeout"] == marketplace_api.UPLOAD_TIMEOUT
        assert result == {"version": 3}

    def test_publish_tolerates_an_empty_body(self, monkeypatch):
        _install(monkeypatch, _Response(204))
        assert (
            MarketplaceClient(BASE, token="tok").publish_spl(
                "cms_spl", b"x", "cms_spl.uvl"
            )
            == {}
        )

    def test_publish_requires_a_token(self, monkeypatch):
        recorder = _install(monkeypatch, _Response(201, {}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).publish_spl("cms_spl", b"x", "cms_spl.uvl")
        assert exc.value.code == marketplace_api.CODE_UNAUTHENTICATED
        assert recorder.calls == []


# ── Rate limiting ───────────────────────────────────────────────────────────


class TestRateLimit:
    """A 429 is the marketplace throttling us, never a credential verdict.

    The GitHub/PyPI boundary answers this question with RegistryError.
    rate_limited; the marketplace boundary has to answer it the same way, or
    every 429 reads as "unexpected error, report it" on the one endpoint that
    servers throttle hardest, the login.
    """

    def test_429_is_classified_and_flagged(self, monkeypatch):
        _install(monkeypatch, _Response(429, {"error": "slow down"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_RATE_LIMITED
        assert exc.value.rate_limited is True
        assert exc.value.status == 429

    def test_retry_after_is_captured(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(429, {"error": "slow down"}, headers={"Retry-After": "30"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.retry_after == "30"

    def test_a_response_without_headers_is_tolerated(self, monkeypatch):
        """Proxies throttle without saying for how long, and stubs have none."""
        _install(monkeypatch, _Response(429, {"error": "slow down"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.retry_after is None

    def test_rate_limited_is_never_a_dead_token(self, monkeypatch):
        """Deleting the stored token over a 429 would lose a working credential."""
        _install(monkeypatch, _Response(429, {"error": "slow down"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.dead_token is False

    def test_a_throttled_login_is_not_reported_as_a_wrong_password(self, monkeypatch):
        """The unauthenticated override must not swallow a throttle."""
        _install(monkeypatch, _Response(429, {"error": "too many attempts"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).login("a@b.c", "pw", "label")
        assert exc.value.code == marketplace_api.CODE_RATE_LIMITED
        assert exc.value.code != marketplace_api.CODE_INVALID_CREDENTIALS

    def test_server_code_classifies_even_on_another_status(self, monkeypatch):
        _install(monkeypatch, _Response(503, {"code": "too_many_requests"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_RATE_LIMITED

    def test_other_errors_are_not_flagged_as_rate_limited(self, monkeypatch):
        _install(monkeypatch, _Response(500, {"error": "boom"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.rate_limited is False


# ── Non-API endpoints ───────────────────────────────────────────────────────

HTML_404 = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
    "<title>Page not found | splent_marketplace_app</title>\n"
    '<link rel="stylesheet" href="/static/css/app.css">\n</head>\n'
    "<body><h1>Page not found</h1></body></html>\n"
)


class TestMarkupBodies:
    """A registry URL that is not a SPLENT API answers with a whole web page.

    Observed live against the dev marketplace before its /api/v1 routes
    existed: the diagnosis was right, but 300 characters of raw HTML were
    pasted underneath it and buried the advice.
    """

    def test_an_html_error_page_is_not_quoted_back(self, monkeypatch):
        _install(monkeypatch, _Response(404, text=HTML_404))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).get_spl("cms_spl")
        assert "<!DOCTYPE" not in str(exc.value)
        assert "stylesheet" not in str(exc.value)
        assert exc.value.server_message == ""

    def test_the_status_is_still_reported(self, monkeypatch):
        _install(monkeypatch, _Response(404, text=HTML_404))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE).get_spl("cms_spl")
        assert exc.value.code == marketplace_api.CODE_NOT_FOUND
        assert "HTTP 404" in str(exc.value)

    def test_an_xml_body_is_treated_the_same(self, monkeypatch):
        _install(monkeypatch, _Response(403, text="<?xml version='1.0'?><Error/>"))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.server_message == ""

    def test_a_plain_text_body_is_still_shown(self, monkeypatch):
        """Only markup is noise, a text/plain message is the server talking."""
        _install(monkeypatch, _Response(503, text="upstream down"))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.server_message == "upstream down"
        assert "upstream down" in str(exc.value)

    def test_a_json_message_is_still_shown(self, monkeypatch):
        _install(monkeypatch, _Response(403, {"error": "missing scope spl:publish"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.server_message == "missing scope spl:publish"

    def test_a_synthesized_message_is_never_attributed_to_the_server(self, monkeypatch):
        _install(monkeypatch, _Response(500, text=""))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert "HTTP 500" in exc.value.message
        assert exc.value.server_message == ""


# ── The refusal contract the marketplace actually speaks ────────────────────
#
# apikeys reserves 401 for a request that carried NO token and answers 403 for
# one that carried a token it will not accept: revoked, expired, or never
# issued. The expired/revoked branches used to live inside `if status == 401`,
# so against this server every one of them fell through to CODE_FORBIDDEN,
# DEAD_TOKEN_CODES was unreachable, forget_dead_token() was dead code, and the
# developer was advised to ask for a missing scope on a credential the server
# had destroyed. Verified live: DELETE /api/v1/auth/tokens/current, then whoami
# with the same token, answers 403 {"error": "This API token has been revoked"}.


class TestTheServersRealRefusals:
    def test_a_revoked_token_at_403_is_dead(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(403, {"error": "x", "code": "token_revoked"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_TOKEN_REVOKED
        assert exc.value.dead_token is True

    def test_an_expired_token_at_403_is_dead(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(403, {"error": "x", "code": "token_expired"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_TOKEN_EXPIRED
        assert exc.value.dead_token is True

    def test_a_token_nobody_issued_at_403_is_dead(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(403, {"error": "x", "code": "invalid_token"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_UNAUTHENTICATED
        assert exc.value.dead_token is True

    def test_a_missing_scope_at_403_is_not_dead(self, monkeypatch):
        """The distinction the whole thing turns on: this token is fine."""
        _install(
            monkeypatch,
            _Response(403, {"error": "x", "code": "insufficient_scope"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_FORBIDDEN
        assert exc.value.dead_token is False

    def test_a_403_with_no_recognisable_reason_keeps_the_credential(
        self, monkeypatch
    ):
        _install(monkeypatch, _Response(403, {"error": "nope"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_FORBIDDEN
        assert exc.value.dead_token is False


class TestRefusalsWithoutACode:
    """An older server that sends prose and no code is still understood.

    English prose only, and that is the point of the codes above: the
    marketplace negotiates Accept-Language on its API routes, so the moment a
    Spanish translation lands these stop matching and the code is all there is.
    """

    def test_revoked_prose_at_403_is_still_read(self, monkeypatch):
        _install(
            monkeypatch,
            _Response(403, {"error": "This API token has been revoked"}),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_TOKEN_REVOKED

    def test_expired_prose_at_403_is_still_read(self, monkeypatch):
        _install(
            monkeypatch, _Response(403, {"error": "This API token has expired"})
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_TOKEN_EXPIRED

    def test_unknown_token_prose_at_403_is_still_read(self, monkeypatch):
        _install(monkeypatch, _Response(403, {"error": "Invalid API token"}))
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_UNAUTHENTICATED

    def test_a_translated_inactive_account_is_still_diagnosed_by_code(
        self, monkeypatch
    ):
        """The best message in the product, made translation proof.

        It used to be selected purely by matching the English substring "not
        active" in a string produced by gettext.
        """
        _install(
            monkeypatch,
            _Response(
                403,
                {
                    "error": "Esta cuenta todavía no está activa.",
                    "code": "account_inactive",
                },
            ),
        )
        with pytest.raises(MarketplaceError) as exc:
            MarketplaceClient(BASE, token="tok").whoami()
        assert exc.value.code == marketplace_api.CODE_ACCOUNT_INACTIVE
        assert exc.value.dead_token is False
