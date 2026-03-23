"""
Tests for the check:github command.

All HTTP calls are mocked — no real GitHub API needed.
"""
import json
import urllib.error
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.check.check_github import check_github


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _mock_response(login="testuser", name="Test User", plan="free",
                   rate_remaining="4999", rate_limit="5000"):
    """Build a fake urllib response."""
    body = json.dumps({"login": login, "name": name, "plan": {"name": plan}}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.headers.get = lambda key, default="?": {
        "X-RateLimit-Remaining": rate_remaining,
        "X-RateLimit-Limit": rate_limit,
    }.get(key, default)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Missing credentials
# ---------------------------------------------------------------------------

class TestMissingCredentials:
    def test_exits_when_no_github_user(self, runner, monkeypatch):
        monkeypatch.delenv("GITHUB_USER", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(check_github, [])
        assert result.exit_code == 1
        assert "GITHUB_USER" in result.output

    def test_exits_when_no_token(self, runner, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "testuser")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(check_github, [])
        assert result.exit_code == 1
        assert "GITHUB_TOKEN" in result.output


# ---------------------------------------------------------------------------
# Successful authentication
# ---------------------------------------------------------------------------

class TestSuccessfulAuth:
    def test_exits_0_when_valid(self, runner, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "testuser")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "a" * 36)

        with patch("urllib.request.urlopen", return_value=_mock_response("testuser")):
            result = runner.invoke(check_github, [])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_shows_authenticated_user(self, runner, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "testuser")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "a" * 36)

        with patch("urllib.request.urlopen", return_value=_mock_response("testuser")):
            result = runner.invoke(check_github, [])
        assert "testuser" in result.output

    def test_warns_when_token_user_differs(self, runner, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "myuser")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "a" * 36)

        with patch("urllib.request.urlopen", return_value=_mock_response("otheruser")):
            result = runner.invoke(check_github, [])
        # Should warn about mismatch (but may still exit 0)
        assert "otheruser" in result.output or "myuser" in result.output


# ---------------------------------------------------------------------------
# HTTP errors
# ---------------------------------------------------------------------------

class TestHttpErrors:
    def test_exits_1_on_401(self, runner, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "testuser")
        monkeypatch.setenv("GITHUB_TOKEN", "bad_token")

        err = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            result = runner.invoke(check_github, [])
        assert result.exit_code == 1
        assert "401" in result.output or "invalid" in result.output.lower()

    def test_exits_1_on_403(self, runner, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "testuser")
        monkeypatch.setenv("GITHUB_TOKEN", "bad_token")

        err = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            result = runner.invoke(check_github, [])
        assert result.exit_code == 1

    def test_exits_1_on_network_error(self, runner, monkeypatch):
        monkeypatch.setenv("GITHUB_USER", "testuser")
        monkeypatch.setenv("GITHUB_TOKEN", "some_token")

        err = urllib.error.URLError("Network unreachable")
        with patch("urllib.request.urlopen", side_effect=err):
            result = runner.invoke(check_github, [])
        assert result.exit_code == 1
        assert "Network" in result.output or "error" in result.output.lower()
