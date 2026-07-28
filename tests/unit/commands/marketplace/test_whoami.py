"""splent whoami — identity, provenance of the token, and the offline story."""

from click.testing import CliRunner

from splent_cli.commands.marketplace.whoami import whoami
from splent_cli.services import credentials, marketplace_api

from .conftest import LOCAL, PROD, error


def _run(args=None):
    return CliRunner().invoke(whoami, args or [])


# ── Logged in ───────────────────────────────────────────────────────────────


class TestLoggedIn:
    def test_prints_registry_identity_scopes_and_expiry(self, api):
        credentials.save(PROD, token="tok-123")

        result = _run()

        assert result.exit_code == 0, result.output
        assert "https://marketplace.splent.io" in result.output
        assert "dev@example.com" in result.output
        assert "spl:publish" in result.output
        assert "2099-01-01T00:00:00+00:00" in result.output
        assert "splent-cli@laptop" in result.output

    def test_reports_the_registry_origin(self, api):
        credentials.save(PROD, token="tok-123")
        assert "(default)" in _run().output

        credentials.save(LOCAL, token="tok-123")
        out = _run(["--registry", "http://localhost:5818"]).output
        assert "(--registry)" in out

    def test_environment_registry_origin_is_named(self, api, monkeypatch):
        monkeypatch.setenv("SPLENT_MARKETPLACE_URL", "http://localhost:5818")
        credentials.save(LOCAL, token="tok-123")
        assert "(SPLENT_MARKETPLACE_URL)" in _run().output

    def test_reports_the_credential_file_as_the_source(self, api, store):
        credentials.save(PROD, token="tok-123")
        result = _run()
        assert f"file {store}" in result.output

    def test_reports_the_environment_variable_as_the_source(self, api, monkeypatch):
        """The line that explains why CI and a laptop disagree."""
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")

        result = _run()

        assert result.exit_code == 0
        assert f"environment variable {credentials.TOKEN_ENV}" in result.output
        assert api.whoami_calls[0]["token"] == "env-token"

    def test_environment_token_wins_over_the_stored_one(self, api, monkeypatch):
        credentials.save(PROD, token="file-token")
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")

        _run()

        assert api.whoami_calls[0]["token"] == "env-token"

    def test_refreshes_the_cached_identity(self, api):
        credentials.save(PROD, token="tok-123", identity="stale@example.com")

        _run()

        entry = credentials.get(PROD)
        assert entry["identity"] == "dev@example.com"
        assert entry["token"] == "tok-123"

    def test_token_is_never_printed(self, api):
        credentials.save(PROD, token="tok-123")
        assert "tok-123" not in _run().output


# ── Not logged in ───────────────────────────────────────────────────────────


class TestNotLoggedIn:
    def test_says_so_and_exits_non_zero(self, api, store):
        result = _run()

        assert result.exit_code == 1
        assert "Not logged in to https://marketplace.splent.io" in result.output
        assert "Run splent login" in result.output
        assert str(store) in result.output
        assert api.whoami_calls == []

    def test_a_token_for_another_registry_does_not_count(self, api):
        credentials.save(LOCAL, token="local-token")
        result = _run()
        assert result.exit_code == 1
        assert "Not logged in" in result.output


# ── Marketplace unreachable ─────────────────────────────────────────────────


class TestUnreachable:
    def test_shows_the_cached_identity_marked_as_cached(self, api):
        credentials.save(
            PROD,
            token="tok-123",
            identity="dev@example.com",
            token_name="splent-cli@laptop",
            scopes=["spl:publish"],
            expires_at="2099-01-01T00:00:00+00:00",
        )
        api.whoami_error = error(marketplace_api.CODE_UNREACHABLE, "connection refused")

        result = _run()

        assert result.exit_code == 1
        assert "Marketplace unreachable" in result.output
        assert "dev@example.com (cached)" in result.output
        assert "spl:publish (cached)" in result.output
        assert "this is not a logout" in result.output
        assert "Not logged in" not in result.output

    def test_keeps_the_stored_credential(self, api):
        credentials.save(PROD, token="tok-123")
        api.whoami_error = error(marketplace_api.CODE_UNREACHABLE, "down")

        _run()

        assert credentials.get(PROD)["token"] == "tok-123"

    def test_environment_token_has_no_cached_identity_to_show(self, api, monkeypatch):
        credentials.save(PROD, token="file-token", identity="someone@example.com")
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")
        api.whoami_error = error(marketplace_api.CODE_UNREACHABLE, "down")

        result = _run()

        assert "No cached identity" in result.output
        assert "someone@example.com" not in result.output


# ── Dead credentials ────────────────────────────────────────────────────────


class TestDeadToken:
    def test_rejected_token_is_forgotten(self, api):
        credentials.save(PROD, token="tok-123")
        api.whoami_error = error(marketplace_api.CODE_UNAUTHENTICATED, status=401)

        result = _run()

        assert result.exit_code == 1
        assert "rejected the token" in result.output
        assert "was removed" in result.output
        assert credentials.get(PROD) is None

    def test_next_run_says_not_logged_in_instead_of_repeating(self, api):
        credentials.save(PROD, token="tok-123")
        api.whoami_error = error(marketplace_api.CODE_TOKEN_EXPIRED, status=401)

        _run()
        second = _run()

        assert "Not logged in" in second.output

    def test_inactive_account_keeps_the_token(self, api):
        """The credential is fine, the account simply is not activated yet."""
        credentials.save(PROD, token="tok-123")
        api.whoami_error = error(marketplace_api.CODE_ACCOUNT_INACTIVE, status=403)

        result = _run()

        assert result.exit_code == 1
        assert "not active yet" in result.output
        assert credentials.get(PROD)["token"] == "tok-123"

    def test_environment_token_cannot_be_deleted_so_it_is_explained(
        self, api, monkeypatch
    ):
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")
        api.whoami_error = error(marketplace_api.CODE_UNAUTHENTICATED, status=401)

        result = _run()

        assert result.exit_code == 1
        assert f"comes from {credentials.TOKEN_ENV}" in result.output

    def test_invalid_registry_url_is_rejected(self, api):
        result = _run(["--registry", "ftp://example.com"])
        assert result.exit_code == 1
        assert "http or https" in result.output


# ── Marketplace throttling ──────────────────────────────────────────────────


class TestRateLimited:
    """A 429 means we could not ask, exactly like being offline.

    The stored credential is untouched and still valid, so whoami must not
    read like a logout and must never delete the token.
    """

    def test_shows_the_cached_identity_and_keeps_the_token(self, api):
        credentials.save(
            PROD,
            token="tok-123",
            identity="dev@example.com",
            scopes=["spl:publish"],
        )
        api.whoami_error = error(
            marketplace_api.CODE_RATE_LIMITED, "slow down", status=429
        )

        result = _run()

        assert result.exit_code == 1
        assert "rate limiting" in result.output
        assert "dev@example.com (cached)" in result.output
        assert "this is not a logout" in result.output
        assert "Not logged in" not in result.output
        assert credentials.get(PROD)["token"] == "tok-123"

    def test_reports_the_retry_after(self, api):
        credentials.save(PROD, token="tok-123", identity="dev@example.com")
        api.whoami_error = error(
            marketplace_api.CODE_RATE_LIMITED,
            "slow down",
            status=429,
            retry_after="60",
        )

        result = _run()

        assert "Wait 60 seconds" in result.output

    def test_the_next_run_still_has_the_credential(self, api):
        credentials.save(PROD, token="tok-123", identity="dev@example.com")
        api.whoami_error = error(
            marketplace_api.CODE_RATE_LIMITED, "slow down", status=429
        )

        _run()
        second = _run()

        assert "Not logged in" not in second.output
