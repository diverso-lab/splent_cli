"""
Tests for the check:pypi command.

All HTTP calls are mocked.
"""
import urllib.error
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.check.check_pypi import check_pypi


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _http_error(code):
    return urllib.error.HTTPError("url", code, "msg", {}, None)


# ---------------------------------------------------------------------------
# Missing credentials
# ---------------------------------------------------------------------------

class TestMissingCredentials:
    def test_exits_when_no_username(self, runner, monkeypatch):
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.delenv("PYPI_PASSWORD", raising=False)
        result = runner.invoke(check_pypi, [])
        assert result.exit_code == 1
        assert "TWINE_USERNAME" in result.output

    def test_exits_when_no_password(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "myuser")
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.delenv("PYPI_PASSWORD", raising=False)
        result = runner.invoke(check_pypi, [])
        assert result.exit_code == 1
        assert "TWINE_PASSWORD" in result.output


# ---------------------------------------------------------------------------
# Valid credentials (PyPI returns 400 = expected for empty upload)
# ---------------------------------------------------------------------------

class TestValidCredentials:
    def test_exits_0_on_400(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "myuser")
        monkeypatch.setenv("TWINE_PASSWORD", "mypassword123")

        with patch("urllib.request.urlopen", side_effect=_http_error(400)):
            result = runner.invoke(check_pypi, [])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_exits_0_on_200(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "myuser")
        monkeypatch.setenv("TWINE_PASSWORD", "mypassword123")

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = runner.invoke(check_pypi, [])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Invalid credentials
# ---------------------------------------------------------------------------

class TestInvalidCredentials:
    def test_exits_1_on_401(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "myuser")
        monkeypatch.setenv("TWINE_PASSWORD", "wrong_password")

        with patch("urllib.request.urlopen", side_effect=_http_error(401)):
            result = runner.invoke(check_pypi, [])
        assert result.exit_code == 1

    def test_exits_1_on_403(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "myuser")
        monkeypatch.setenv("TWINE_PASSWORD", "wrong_password")

        with patch("urllib.request.urlopen", side_effect=_http_error(403)):
            result = runner.invoke(check_pypi, [])
        assert result.exit_code == 1

    def test_exits_1_on_network_error(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "myuser")
        monkeypatch.setenv("TWINE_PASSWORD", "some_pass")

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("unreachable")):
            result = runner.invoke(check_pypi, [])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Token format check
# ---------------------------------------------------------------------------

class TestTokenFormat:
    def test_warns_if_token_user_without_pypi_prefix(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "not_a_pypi_token")

        with patch("urllib.request.urlopen", side_effect=_http_error(400)):
            result = runner.invoke(check_pypi, [])
        assert "pypi-" in result.output or "invalid" in result.output.lower() or "may be" in result.output

    def test_correct_token_format_acknowledged(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-" + "a" * 30)

        with patch("urllib.request.urlopen", side_effect=_http_error(400)):
            result = runner.invoke(check_pypi, [])
        assert "correct" in result.output.lower() or "pypi-" in result.output


# ---------------------------------------------------------------------------
# --test flag (TestPyPI)
# ---------------------------------------------------------------------------

class TestTestPyPI:
    def test_uses_testpypi_url(self, runner, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "myuser")
        monkeypatch.setenv("TWINE_PASSWORD", "mypassword123")

        with patch("urllib.request.urlopen", side_effect=_http_error(400)):
            result = runner.invoke(check_pypi, ["--test"])
        assert result.exit_code == 0
        assert "TestPyPI" in result.output
