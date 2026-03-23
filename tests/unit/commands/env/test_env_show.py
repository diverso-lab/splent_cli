"""
Tests for the env:show command.

env:show reads .env and compares each variable against the live shell
via `bash -c "echo $VAR"`. We mock subprocess.run to control the shell output.
"""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.env.env_show import env_show, _mask


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _mask() helper — pure logic
# ---------------------------------------------------------------------------

class TestMask:
    def test_long_sensitive_value_is_masked(self):
        result = _mask("my_secret_value_123", "API_KEY")
        assert "my_sec" in result
        assert "..." in result
        assert "123" in result

    def test_short_sensitive_value_is_asterisks(self):
        result = _mask("short", "TOKEN")
        assert result == "********"

    def test_non_sensitive_value_unchanged(self):
        result = _mask("hello_world", "APP_NAME")
        assert result == "hello_world"

    def test_password_key_is_masked(self):
        result = _mask("supersecret12345", "DB_PASSWORD")
        assert "..." in result

    def test_secret_key_is_masked(self):
        result = _mask("my_secret_abcdefg", "SECRET_KEY")
        assert "..." in result


# ---------------------------------------------------------------------------
# No .env file
# ---------------------------------------------------------------------------

class TestNoEnvFile:
    def test_exits_when_no_env_file(self, runner, workspace):
        result = runner.invoke(env_show, [])
        assert result.exit_code == 1
        assert "No .env" in result.output


# ---------------------------------------------------------------------------
# Variable comparison scenarios
# ---------------------------------------------------------------------------

class TestVariableComparison:
    def _invoke_with_env(self, runner, workspace, env_content, shell_values: dict):
        """Helper: write .env and mock bash output per-variable."""
        (workspace / ".env").write_text(env_content)

        def fake_run(cmd, **kwargs):
            # cmd is ["bash", "-c", "echo $VAR"]
            var_name = cmd[2].replace("echo $", "")
            value = shell_values.get(var_name, "")
            return MagicMock(returncode=0, stdout=value + "\n", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(env_show, [])
        return result

    def test_loaded_and_matching_shows_checkmark(self, runner, workspace):
        result = self._invoke_with_env(
            runner, workspace,
            "APP_NAME=myapp\n",
            {"APP_NAME": "myapp"},
        )
        assert result.exit_code == 0
        assert "APP_NAME" in result.output
        # ✅ appears for matching
        assert "✅" in result.output

    def test_not_loaded_shows_warning(self, runner, workspace):
        result = self._invoke_with_env(
            runner, workspace,
            "MISSING_VAR=somevalue\n",
            {"MISSING_VAR": ""},  # empty → not loaded
        )
        assert result.exit_code == 0
        assert "not loaded" in result.output

    def test_differs_shows_diff_message(self, runner, workspace):
        result = self._invoke_with_env(
            runner, workspace,
            "APP_ENV=dev\n",
            {"APP_ENV": "prod"},  # different value
        )
        assert result.exit_code == 0
        assert "differs" in result.output.lower() or "loaded but differs" in result.output

    def test_shows_tip_at_end(self, runner, workspace):
        result = self._invoke_with_env(
            runner, workspace,
            "APP_NAME=myapp\n",
            {"APP_NAME": "myapp"},
        )
        assert "source .env" in result.output

    def test_skips_comment_lines(self, runner, workspace):
        result = self._invoke_with_env(
            runner, workspace,
            "# This is a comment\nAPP_NAME=myapp\n",
            {"APP_NAME": "myapp"},
        )
        assert result.exit_code == 0
        # Comment should not appear as a variable
        assert "This is a comment" not in result.output

    def test_skips_lines_without_equals(self, runner, workspace):
        result = self._invoke_with_env(
            runner, workspace,
            "NOEQUALS\nAPP_NAME=myapp\n",
            {"APP_NAME": "myapp"},
        )
        assert result.exit_code == 0
        assert "NOEQUALS" not in result.output

    def test_subprocess_exception_treated_as_empty(self, runner, workspace):
        """If bash fails (e.g. not found), variable treated as not loaded."""
        (workspace / ".env").write_text("MY_VAR=value\n")

        with patch("subprocess.run", side_effect=FileNotFoundError("bash not found")):
            result = runner.invoke(env_show, [])

        assert result.exit_code == 0
        assert "not loaded" in result.output
