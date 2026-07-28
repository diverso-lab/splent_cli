"""splent login — prompts, validation before writing, and the missing --token."""

from click.testing import CliRunner

from splent_cli.commands.marketplace.login import login
from splent_cli.services import credentials, marketplace_api

from .conftest import LOCAL, PROD, error

CREDS = "dev@example.com\nhunter2\n"


def _run(args=None, input_=CREDS):
    return CliRunner().invoke(login, args or [], input=input_)


# ── Happy path ──────────────────────────────────────────────────────────────


class TestLogin:
    def test_prompts_and_stores_the_token(self, api, store):
        result = _run()

        assert result.exit_code == 0, result.output
        assert "Logged in to https://marketplace.splent.io" in result.output
        entry = credentials.get(PROD)
        assert entry["token"] == "tok-123"
        assert entry["identity"] == "dev@example.com"
        assert entry["scopes"] == ["spl:publish"]

    def test_sends_the_typed_credentials(self, api):
        _run()
        call = api.login_calls[0]
        assert call["email"] == "dev@example.com"
        assert call["password"] == "hunter2"
        assert call["ttl"] == marketplace_api.DEFAULT_TTL_DAYS
        assert call["name"].startswith("splent-cli")

    def test_name_option_labels_the_token(self, api):
        _run(["--name", "ci-runner"])
        assert api.login_calls[0]["name"] == "ci-runner"

    def test_registry_option_targets_another_marketplace(self, api):
        result = _run(["--registry", "http://localhost:5818/"])
        assert result.exit_code == 0, result.output
        assert api.login_calls[0]["base_url"] == LOCAL
        assert credentials.get(LOCAL)["token"] == "tok-123"
        assert credentials.get(PROD) is None

    def test_registries_coexist(self, api):
        _run()
        _run(["--registry", "http://localhost:5818"])
        assert credentials.registries() == sorted([PROD, LOCAL])

    def test_secrets_are_never_echoed(self, api):
        result = _run()
        assert "hunter2" not in result.output
        assert "tok-123" not in result.output

    def test_reports_where_the_token_was_stored(self, api, store):
        result = _run()
        assert str(store) in result.output
        assert "0600" in result.output

    def test_warns_when_the_environment_token_would_win(self, api, monkeypatch):
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")
        result = _run()
        assert result.exit_code == 0
        assert credentials.TOKEN_ENV in result.output
        assert "takes precedence" in result.output


# ── Validation before writing ───────────────────────────────────────────────


class TestValidatesBeforeWriting:
    def test_whoami_is_called_with_the_new_token(self, api):
        _run()
        assert api.whoami_calls[0]["token"] == "tok-123"

    def test_a_token_that_fails_whoami_is_not_stored(self, api):
        api.whoami_error = error(marketplace_api.CODE_UNAUTHENTICATED, status=401)
        result = _run()

        assert result.exit_code == 1
        assert "Nothing was stored." in result.output
        assert credentials.get(PROD) is None

    def test_failed_login_never_reaches_whoami(self, api):
        api.login_error = error(marketplace_api.CODE_INVALID_CREDENTIALS, status=401)
        result = _run()

        assert result.exit_code == 1
        assert api.whoami_calls == []
        assert credentials.get(PROD) is None


# ── Actionable errors ───────────────────────────────────────────────────────


class TestErrors:
    def test_wrong_password(self, api):
        api.login_error = error(marketplace_api.CODE_INVALID_CREDENTIALS, status=401)
        result = _run()
        assert "rejected that e-mail and password" in result.output
        assert "reset it" in result.output

    def test_inactive_account_explains_the_activation_step(self, api):
        api.login_error = error(marketplace_api.CODE_ACCOUNT_INACTIVE, status=403)
        result = _run()

        assert result.exit_code == 1
        assert "not active yet" in result.output
        assert "administrator" in result.output
        assert credentials.get(PROD) is None

    def test_unreachable_marketplace_names_both_development_urls(self, api):
        api.login_error = error(marketplace_api.CODE_UNREACHABLE, "connection refused")
        result = _run()

        assert result.exit_code == 1
        assert "Could not reach the marketplace" in result.output
        assert "http://splent_marketplace_app_web:5000" in result.output
        assert "http://localhost:5818" in result.output

    def test_unexpected_status_is_reported(self, api):
        api.login_error = marketplace_api.MarketplaceError(
            "teapot", status=418, code=marketplace_api.CODE_UNEXPECTED
        )
        result = _run()
        assert result.exit_code == 1
        assert "Unexpected marketplace error (HTTP 418)" in result.output

    def test_invalid_registry_url_is_rejected_up_front(self, api):
        result = _run(["--registry", "ftp://example.com"])
        assert result.exit_code == 1
        assert "http or https" in result.output
        assert api.login_calls == []


# ── Token from stdin ────────────────────────────────────────────────────────


class TestTokenStdin:
    def test_reads_the_token_and_validates_it(self, api):
        result = CliRunner().invoke(login, ["--token-stdin"], input="ci-token\n")

        assert result.exit_code == 0, result.output
        assert api.login_calls == []
        assert api.whoami_calls[0]["token"] == "ci-token"
        assert credentials.get(PROD)["token"] == "ci-token"

    def test_dead_pasted_token_is_not_stored(self, api):
        api.whoami_error = error(marketplace_api.CODE_TOKEN_EXPIRED, status=401)
        result = CliRunner().invoke(login, ["--token-stdin"], input="old-token\n")

        assert result.exit_code == 1
        assert "expired" in result.output
        assert credentials.get(PROD) is None

    def test_empty_stdin_is_an_actionable_error(self, api):
        result = CliRunner().invoke(login, ["--token-stdin"], input="")
        assert result.exit_code == 1
        assert "No token arrived on stdin" in result.output

    def test_multi_value_stdin_is_refused(self, api):
        result = CliRunner().invoke(
            login, ["--token-stdin"], input="token extra-word\n"
        )
        assert result.exit_code == 1
        assert "not a single value" in result.output

    def test_name_is_reported_as_ignored(self, api):
        result = CliRunner().invoke(
            login, ["--token-stdin", "--name", "x"], input="ci-token\n"
        )
        assert "ignored with --token-stdin" in result.output


# ── The option that must not exist ──────────────────────────────────────────


class TestNoTokenOption:
    def test_there_is_no_token_flag(self, api):
        """A secret in argv lands in the shell history and the process table."""
        result = CliRunner().invoke(login, ["--token", "tok-123"])
        assert result.exit_code == 2
        assert "No such option" in result.output
        assert api.whoami_calls == []

    def test_help_only_advertises_token_stdin(self):
        result = CliRunner().invoke(login, ["--help"])
        assert "--token-stdin" in result.output
        assert "--token " not in result.output


# ── Throttled ───────────────────────────────────────────────────────────────


class TestRateLimited:
    """Login is the endpoint servers throttle hardest, against password guessing.

    The developer must be told to wait, not that their password is wrong and
    not to report a bug.
    """

    def test_says_to_wait_and_stores_nothing(self, api):
        api.login_error = error(
            marketplace_api.CODE_RATE_LIMITED, "too many attempts", status=429
        )

        result = _run()

        assert result.exit_code == 1
        assert "rate limiting" in result.output
        assert credentials.get(PROD) is None

    def test_uses_the_retry_after_the_server_sent(self, api):
        api.login_error = error(
            marketplace_api.CODE_RATE_LIMITED,
            "slow down",
            status=429,
            retry_after="45",
        )

        result = _run()

        assert "Wait 45 seconds" in result.output

    def test_does_not_blame_the_password(self, api):
        api.login_error = error(
            marketplace_api.CODE_RATE_LIMITED, "slow down", status=429
        )

        result = _run()

        assert "Nothing is wrong with your password" in result.output
        assert "rejected that e-mail and password" not in result.output

    def test_does_not_ask_the_developer_to_report_a_bug(self, api):
        api.login_error = error(
            marketplace_api.CODE_RATE_LIMITED, "slow down", status=429
        )

        result = _run()

        assert "report it if it happens again" not in result.output


# ── A URL that is not a SPLENT marketplace ──────────────────────────────────


class TestNonApiEndpoint:
    def test_no_html_is_pasted_into_the_terminal(self, api):
        """Observed live: the advice was buried under an HTML error page."""
        api.login_error = marketplace_api.MarketplaceError(
            "The marketplace answered HTTP 404.",
            status=404,
            code=marketplace_api.CODE_NOT_FOUND,
            server_message="",
        )

        result = _run()

        assert "<!DOCTYPE" not in result.output
        assert "Server said" not in result.output
        assert "nothing at that address" in result.output
        assert credentials.get(PROD) is None
