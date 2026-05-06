"""
Tests for marketplace login/logout commands.
"""

from click.testing import CliRunner
import pytest
from unittest.mock import patch

from splent_cli.commands.marketplace_login import marketplace_login
from splent_cli.commands.marketplace_logout import marketplace_logout
from splent_cli.services import marketplace


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture(autouse=True)
def clear_token(monkeypatch):
    monkeypatch.delenv("SPLENT_API_TOKEN", raising=False)
    monkeypatch.delenv("SPLENT_API_URL", raising=False)


class TestMarketplaceLogin:
    def test_login_saves_token_in_env_file(self, runner, workspace):
        with patch(
            "splent_cli.commands.marketplace_login.marketplace.validate_api_token",
            return_value=True,
        ):
            result = runner.invoke(marketplace_login, ["--token", "abc123"])

        content = (workspace / ".env").read_text()
        assert result.exit_code == 0
        assert "SPLENT_API_TOKEN=abc123" in content
        assert "SPLENT_API_URL=https://api.splent.io" in content
        assert "login" in result.output.lower()

    def test_login_prompts_for_token(self, runner, workspace):
        with patch(
            "splent_cli.commands.marketplace_login.marketplace.validate_api_token",
            return_value=True,
        ):
            result = runner.invoke(marketplace_login, input="secret\n")

        content = (workspace / ".env").read_text()
        assert result.exit_code == 0
        assert "SPLENT_API_TOKEN=secret" in content

    def test_login_can_output_shell_export(self, runner, workspace):
        with patch(
            "splent_cli.commands.marketplace_login.marketplace.validate_api_token",
            return_value=True,
        ):
            result = runner.invoke(
                marketplace_login, ["--token", "abc123", "--shell"]
            )

        assert result.exit_code == 0
        assert "export SPLENT_API_URL=https://api.splent.io" in result.output
        assert "export SPLENT_API_TOKEN=abc123" in result.output

    def test_login_with_url_saves_api_url(self, runner, workspace):
        with patch(
            "splent_cli.commands.marketplace_login.marketplace.validate_api_token",
            return_value=True,
        ) as validate:
            result = runner.invoke(
                marketplace_login,
                ["--token", "abc123", "--url", "http://localhost:5000"],
            )

        content = (workspace / ".env").read_text()
        assert result.exit_code == 0
        assert "SPLENT_API_URL=http://localhost:5000" in content
        validate.assert_called_once_with("http://localhost:5000", "abc123")

    def test_invalid_login_does_not_save_token(self, runner, workspace):
        with patch(
            "splent_cli.commands.marketplace_login.marketplace.validate_api_token",
            return_value=False,
        ):
            result = runner.invoke(marketplace_login, ["--token", "bad"])

        assert result.exit_code == 1
        assert "invalid" in result.output.lower()
        assert not (workspace / ".env").exists()

    def test_connection_error_shows_clear_message(self, runner, workspace):
        with patch(
            "splent_cli.commands.marketplace_login.marketplace.validate_api_token",
            side_effect=marketplace.MarketplaceLoginError("Could not connect"),
        ):
            result = runner.invoke(marketplace_login, ["--token", "abc123"])

        assert result.exit_code == 1
        assert "could not connect" in result.output.lower()
        assert not (workspace / ".env").exists()


class TestMarketplaceLogout:
    def test_logout_removes_token(self, runner, workspace, monkeypatch):
        (workspace / ".env").write_text(
            "SPLENT_APP=test_app\nSPLENT_API_TOKEN=abc123\n"
        )
        monkeypatch.setenv("SPLENT_API_TOKEN", "abc123")

        result = runner.invoke(marketplace_logout)

        content = (workspace / ".env").read_text()
        assert result.exit_code == 0
        assert "SPLENT_API_TOKEN" not in content
        assert "logout" in result.output.lower()

    def test_logout_can_output_shell_unset(self, runner, workspace):
        result = runner.invoke(marketplace_logout, ["--shell"])

        assert result.exit_code == 0
        assert f"unset {marketplace.MARKETPLACE_TOKEN_VAR}" in result.output
