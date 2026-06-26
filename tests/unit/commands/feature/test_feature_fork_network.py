"""Tests for feature:fork — GitHub POST/poll network hardening.

Covers (hardened behaviors first):
  * Missing GITHUB_TOKEN / GITHUB_USER -> clean error, exit 1.
  * POST raises requests.RequestException -> clean message, no hang/traceback.
  * POST returns rate-limit / error HTTP status -> clean message, no traceback.
  * POST returns 201 but error/empty JSON body (no html_url) -> clean
    message, no KeyError/traceback.
  * POST returns a body where .json() raises ValueError -> clean message.
  * Poll GET raises requests.RequestException -> tolerated, no traceback.
Plus core happy-path: successful fork invokes clone with the right full_name.

All network calls are mocked at the requests boundary; the downstream
feature_clone invocation is patched so no real git/network runs.
"""

import requests
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.feature.feature_fork import feature_fork


def _ok_post_response():
    """A successful fork POST response."""
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"html_url": "https://github.com/octocat/feat"}
    resp.text = "{}"
    return resp


def _ok_get_response():
    """A poll GET that reports the fork is ready."""
    resp = MagicMock()
    resp.status_code = 200
    return resp


class TestCredentialsRequired:
    def test_missing_token_and_user_clean_exit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_USER", raising=False)
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(feature_fork, ["splent_feature_auth"])
        assert result.exit_code == 1
        assert "GITHUB_TOKEN" in result.output
        assert "Traceback" not in result.output


class TestForkPostErrors:
    def test_post_request_exception_clean_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        runner = CliRunner(mix_stderr=False)

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                side_effect=requests.ConnectionError("boom"),
            ),
            patch("splent_cli.commands.feature.feature_fork.requests.get") as mock_get,
        ):
            result = runner.invoke(feature_fork, ["splent_feature_auth"])

        # Network failure surfaced cleanly, no hang/traceback.
        assert result.exit_code == 2
        assert "Could not reach GitHub" in result.output
        assert "Traceback" not in result.output
        assert "ConnectionError" not in result.output.replace(
            "Could not reach GitHub", ""
        )
        # Must not proceed to poll after POST failed.
        mock_get.assert_not_called()

    def test_post_rate_limited_clean_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        runner = CliRunner(mix_stderr=False)

        resp = MagicMock()
        resp.status_code = 403
        resp.text = "rate limit exceeded"

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                return_value=resp,
            ),
            patch("splent_cli.commands.feature.feature_fork.requests.get"),
        ):
            result = runner.invoke(feature_fork, ["splent_feature_auth"])

        assert result.exit_code == 2
        assert "rate limit" in result.output.lower()
        assert "Traceback" not in result.output

    def test_post_unexpected_status_clean_message(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        runner = CliRunner(mix_stderr=False)

        resp = MagicMock()
        resp.status_code = 404
        resp.text = "Not Found"

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                return_value=resp,
            ),
            patch("splent_cli.commands.feature.feature_fork.requests.get"),
        ):
            result = runner.invoke(feature_fork, ["nope"])

        assert result.exit_code == 2
        assert "Fork failed" in result.output
        assert "404" in result.output
        assert "Traceback" not in result.output


class TestForkPostBadBody:
    def test_empty_json_body_no_keyerror(self, tmp_path, monkeypatch):
        """201 but JSON has no html_url -> clean message, not KeyError."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        runner = CliRunner(mix_stderr=False)

        resp = MagicMock()
        resp.status_code = 201
        resp.json.return_value = {}
        resp.text = "{}"

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                return_value=resp,
            ),
            patch("splent_cli.commands.feature.feature_fork.requests.get"),
        ):
            result = runner.invoke(feature_fork, ["splent_feature_auth"])

        assert result.exit_code == 2
        assert "did not contain the expected repository URL" in result.output
        assert "Traceback" not in result.output
        assert "KeyError" not in result.output

    def test_invalid_json_body_no_traceback(self, tmp_path, monkeypatch):
        """201 but body is not JSON (.json() raises) -> clean message."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        runner = CliRunner(mix_stderr=False)

        resp = MagicMock()
        resp.status_code = 201
        resp.json.side_effect = ValueError("no json")
        resp.text = "<html>oops</html>"

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                return_value=resp,
            ),
            patch("splent_cli.commands.feature.feature_fork.requests.get"),
        ):
            result = runner.invoke(feature_fork, ["splent_feature_auth"])

        assert result.exit_code == 2
        assert "did not contain the expected repository URL" in result.output
        assert "Traceback" not in result.output
        assert "ValueError" not in result.output


class TestPollHandlesErrors:
    def test_poll_get_exception_is_tolerated(self, tmp_path, monkeypatch):
        """A poll GET that raises is caught; flow still reaches clone."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        # Avoid real time.sleep delaying the test loop.
        monkeypatch.setattr(
            "splent_cli.commands.feature.feature_fork.time.sleep",
            lambda *a, **k: None,
        )
        runner = CliRunner(mix_stderr=False)

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                return_value=_ok_post_response(),
            ),
            patch(
                "splent_cli.commands.feature.feature_fork.requests.get",
                side_effect=requests.ConnectionError("flaky"),
            ),
            patch("splent_cli.commands.feature.feature_fork.feature_clone"),
        ):
            result = runner.invoke(feature_fork, ["splent_feature_auth"])

        # Polling errors must not crash the command.
        assert result.exit_code == 0
        assert "Traceback" not in result.output
        assert "ConnectionError" not in result.output.replace("Waiting for GitHub", "")
        # The "may still be processing" fallthrough message is shown.
        assert "still" in result.output.lower() or "Waiting" in result.output


class TestForkHappyPath:
    def test_successful_fork_invokes_clone(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "tok")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        runner = CliRunner(mix_stderr=False)

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                return_value=_ok_post_response(),
            ),
            patch(
                "splent_cli.commands.feature.feature_fork.requests.get",
                return_value=_ok_get_response(),
            ),
            patch(
                "splent_cli.commands.feature.feature_fork.feature_clone"
            ) as mock_clone,
        ):
            result = runner.invoke(
                feature_fork, ["splent_feature_auth", "--version", "v2.0.0"]
            )

        assert result.exit_code == 0
        assert "Fork created" in result.output
        assert "Fork is ready" in result.output
        # clone invoked with the forked full_name@version
        assert mock_clone.called
        called_kwargs = mock_clone.call_args.kwargs
        assert called_kwargs.get("full_name") == ("octocat/splent_feature_auth@v2.0.0")

    def test_token_never_in_argv_or_output(self, tmp_path, monkeypatch):
        """The GITHUB_TOKEN secret must not leak into command output."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("GITHUB_TOKEN", "super_secret_value")
        monkeypatch.setenv("GITHUB_USER", "octocat")
        runner = CliRunner(mix_stderr=False)

        with (
            patch(
                "splent_cli.commands.feature.feature_fork.requests.post",
                return_value=_ok_post_response(),
            ),
            patch(
                "splent_cli.commands.feature.feature_fork.requests.get",
                return_value=_ok_get_response(),
            ),
            patch("splent_cli.commands.feature.feature_fork.feature_clone"),
        ):
            result = runner.invoke(feature_fork, ["splent_feature_auth"])

        assert "super_secret_value" not in result.output
