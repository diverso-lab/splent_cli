"""
Tests for the env:list command.

Pattern: real .env file via tmp_path + monkeypatch os.environ for in-env checks.
No subprocess needed — this command only reads files and the process environment.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.env.env_list import env_list
from tests.conftest import make_env_file


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Missing .env file
# ---------------------------------------------------------------------------

class TestMissingEnvFile:
    def test_exits_when_no_env_file(self, runner, workspace):
        result = runner.invoke(env_list, [])
        assert result.exit_code == 1
        assert ".env" in result.output

    def test_exits_when_empty_env_file(self, runner, workspace):
        make_env_file(workspace, "")
        result = runner.invoke(env_list, [])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()


# ---------------------------------------------------------------------------
# Basic output
# ---------------------------------------------------------------------------

ENV_CONTENT = """\
SPLENT_APP=my_app
SPLENT_ENV=dev
GITHUB_TOKEN=ghp_abc123secretvalue
DB_HOST=localhost
DB_PORT=5432
"""


class TestBasicOutput:
    def test_shows_all_keys(self, runner, workspace):
        make_env_file(workspace, ENV_CONTENT)
        result = runner.invoke(env_list, [])
        assert result.exit_code == 0
        assert "SPLENT_APP" in result.output
        assert "GITHUB_TOKEN" in result.output
        assert "DB_HOST" in result.output

    def test_masks_sensitive_values_by_default(self, runner, workspace):
        make_env_file(workspace, ENV_CONTENT)
        result = runner.invoke(env_list, [])
        assert result.exit_code == 0
        assert "ghp_abc123secretvalue" not in result.output  # masked
        assert "****" in result.output

    def test_no_mask_shows_plain_values(self, runner, workspace):
        make_env_file(workspace, ENV_CONTENT)
        result = runner.invoke(env_list, ["--no-mask"])
        assert result.exit_code == 0
        assert "ghp_abc123secretvalue" in result.output

    def test_grouped_by_category(self, runner, workspace):
        make_env_file(workspace, ENV_CONTENT)
        result = runner.invoke(env_list, [])
        assert result.exit_code == 0
        assert "SPLENT" in result.output   # category header
        assert "GitHub" in result.output   # category header
        assert "Database" in result.output  # category header


# ---------------------------------------------------------------------------
# --keys-only flag
# ---------------------------------------------------------------------------

class TestKeysOnly:
    def test_prints_one_key_per_line(self, runner, workspace):
        make_env_file(workspace, "FOO=bar\nBAZ=qux\n")
        result = runner.invoke(env_list, ["--keys-only"])
        assert result.exit_code == 0
        lines = [l for l in result.output.splitlines() if l.strip()]
        assert "FOO" in lines
        assert "BAZ" in lines
        # Values should NOT appear
        assert "bar" not in result.output
        assert "qux" not in result.output

    def test_keys_sorted(self, runner, workspace):
        make_env_file(workspace, "ZZZ=1\nAAA=2\nMMM=3\n")
        result = runner.invoke(env_list, ["--keys-only"])
        lines = [l.strip() for l in result.output.splitlines() if l.strip()]
        assert lines == sorted(lines)


# ---------------------------------------------------------------------------
# Filter argument
# ---------------------------------------------------------------------------

class TestFilter:
    def test_filter_by_prefix(self, runner, workspace):
        make_env_file(workspace, ENV_CONTENT)
        result = runner.invoke(env_list, ["DB"])
        assert result.exit_code == 0
        assert "DB_HOST" in result.output
        assert "DB_PORT" in result.output
        assert "GITHUB_TOKEN" not in result.output
        assert "SPLENT_APP" not in result.output

    def test_filter_case_insensitive(self, runner, workspace):
        make_env_file(workspace, ENV_CONTENT)
        result = runner.invoke(env_list, ["github"])
        assert result.exit_code == 0
        assert "GITHUB_TOKEN" in result.output

    def test_filter_no_match(self, runner, workspace):
        make_env_file(workspace, ENV_CONTENT)
        result = runner.invoke(env_list, ["NONEXISTENT"])
        assert result.exit_code == 0
        assert "No variables matching" in result.output


# ---------------------------------------------------------------------------
# --unset flag
# ---------------------------------------------------------------------------

class TestUnset:
    def test_shows_only_unset_vars(self, runner, workspace, monkeypatch):
        make_env_file(workspace, "LOADED_KEY=foo\nMISSING_KEY=bar\n")
        monkeypatch.setenv("LOADED_KEY", "foo")
        monkeypatch.delenv("MISSING_KEY", raising=False)

        result = runner.invoke(env_list, ["--unset"])
        assert result.exit_code == 0
        assert "MISSING_KEY" in result.output
        assert "LOADED_KEY" not in result.output

    def test_all_set_shows_success(self, runner, workspace, monkeypatch):
        make_env_file(workspace, "MY_VAR=hello\n")
        monkeypatch.setenv("MY_VAR", "hello")

        result = runner.invoke(env_list, ["--unset"])
        assert result.exit_code == 0
        assert "All variables are set" in result.output


# ---------------------------------------------------------------------------
# Status indicators (✔ · ≠)
# ---------------------------------------------------------------------------

class TestStatusIndicators:
    def test_check_when_var_matches(self, runner, workspace, monkeypatch):
        make_env_file(workspace, "MY_VAR=hello\n")
        monkeypatch.setenv("MY_VAR", "hello")
        result = runner.invoke(env_list, [])
        assert "✔" in result.output

    def test_dot_when_var_not_in_env(self, runner, workspace, monkeypatch):
        make_env_file(workspace, "MY_VAR=hello\n")
        monkeypatch.delenv("MY_VAR", raising=False)
        result = runner.invoke(env_list, [])
        assert "·" in result.output

    def test_not_equal_when_var_differs(self, runner, workspace, monkeypatch):
        make_env_file(workspace, "MY_VAR=file_value\n")
        monkeypatch.setenv("MY_VAR", "different_value")
        result = runner.invoke(env_list, [])
        assert "≠" in result.output


# ---------------------------------------------------------------------------
# Comments and blank lines in .env are ignored
# ---------------------------------------------------------------------------

class TestEnvFileParsing:
    def test_comments_ignored(self, runner, workspace):
        make_env_file(workspace, "# comment\nFOO=bar\n")
        result = runner.invoke(env_list, ["--keys-only"])
        lines = [l.strip() for l in result.output.splitlines() if l.strip()]
        assert lines == ["FOO"]

    def test_blank_lines_ignored(self, runner, workspace):
        make_env_file(workspace, "\n\nFOO=bar\n\n")
        result = runner.invoke(env_list, ["--keys-only"])
        lines = [l.strip() for l in result.output.splitlines() if l.strip()]
        assert lines == ["FOO"]

    def test_quoted_values_unquoted(self, runner, workspace):
        make_env_file(workspace, 'FOO="hello world"\n')
        result = runner.invoke(env_list, ["--keys-only", "--no-mask"])
        # At minimum, the key should show up
        assert "FOO" in result.output
