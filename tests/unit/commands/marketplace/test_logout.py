"""splent logout — best-effort revoke, guaranteed local delete, exit 0."""

from click.testing import CliRunner

from splent_cli.commands.marketplace.logout import logout
from splent_cli.services import credentials, marketplace_api

from .conftest import LOCAL, PROD, error


def _run(args=None):
    return CliRunner().invoke(logout, args or [])


class TestLogout:
    def test_revokes_then_deletes(self, api):
        credentials.save(PROD, token="tok-123")

        result = _run()

        assert result.exit_code == 0, result.output
        assert api.revoke_calls[0]["token"] == "tok-123"
        assert api.revoke_calls[0]["base_url"] == PROD
        assert "Token revoked on the server." in result.output
        assert "Logged out of https://marketplace.splent.io" in result.output
        assert credentials.get(PROD) is None

    def test_nothing_stored_is_not_an_error(self, api):
        result = _run()

        assert result.exit_code == 0
        assert "Nothing stored for https://marketplace.splent.io" in result.output
        assert api.revoke_calls == []

    def test_local_delete_happens_even_when_revoke_fails(self, api):
        credentials.save(PROD, token="tok-123")
        api.revoke_error = error(marketplace_api.CODE_UNREACHABLE, "connection refused")

        result = _run()

        assert result.exit_code == 0, result.output
        assert "removing it locally anyway" in result.output
        assert credentials.get(PROD) is None

    def test_dead_token_needs_no_manual_cleanup_advice(self, api):
        credentials.save(PROD, token="tok-123")
        api.revoke_error = error(marketplace_api.CODE_TOKEN_REVOKED, status=401)

        result = _run()

        assert result.exit_code == 0
        assert "still listed there" not in result.output
        assert credentials.get(PROD) is None

    def test_unreachable_marketplace_advises_manual_revocation(self, api):
        credentials.save(PROD, token="tok-123")
        api.revoke_error = error(marketplace_api.CODE_UNREACHABLE, "down")

        result = _run()

        assert "still listed there" in result.output

    def test_only_the_named_registry_is_touched(self, api):
        credentials.save(PROD, token="prod-token")
        credentials.save(LOCAL, token="local-token")

        result = _run(["--registry", "http://localhost:5818"])

        assert result.exit_code == 0
        assert api.revoke_calls[0]["token"] == "local-token"
        assert credentials.get(LOCAL) is None
        assert credentials.get(PROD)["token"] == "prod-token"

    def test_environment_token_is_reported_not_revoked(self, api, monkeypatch):
        """A CI token is not managed by the CLI, so it is never revoked here."""
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")

        result = _run()

        assert result.exit_code == 0
        assert api.revoke_calls == []
        assert credentials.TOKEN_ENV in result.output
        assert "Unset it" in result.output

    def test_stored_token_is_revoked_even_when_the_environment_has_one(
        self, api, monkeypatch
    ):
        credentials.save(PROD, token="file-token")
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")

        result = _run()

        assert api.revoke_calls[0]["token"] == "file-token"
        assert "still coming from" in result.output
        assert credentials.get(PROD) is None

    def test_token_is_never_printed(self, api):
        credentials.save(PROD, token="tok-123")
        result = _run()
        assert "tok-123" not in result.output

    def test_invalid_registry_url_is_rejected(self, api):
        result = _run(["--registry", "ftp://example.com"])
        assert result.exit_code == 1
        assert "http or https" in result.output
