"""Tests for pure helper functions in env_set.py."""
from click.testing import CliRunner

from splent_cli.commands.env.env_set import (
    load_env,
    write_env,
    set_var,
    env_set_group,
)


# ---------------------------------------------------------------------------
# load_env
# ---------------------------------------------------------------------------

class TestLoadEnv:
    def test_returns_empty_when_no_env_file(self, workspace):
        result = load_env()
        assert result == {}

    def test_parses_key_value_pairs(self, workspace):
        (workspace / ".env").write_text("KEY=value\nOTHER=thing\n")
        result = load_env()
        assert result["KEY"] == "value"
        assert result["OTHER"] == "thing"

    def test_ignores_comment_lines(self, workspace):
        (workspace / ".env").write_text("# comment\nKEY=value\n")
        result = load_env()
        assert "# comment" not in result
        assert result["KEY"] == "value"

    def test_strips_whitespace_from_values(self, workspace):
        (workspace / ".env").write_text("KEY=  spaced  \n")
        result = load_env()
        assert result["KEY"] == "spaced"

    def test_splits_on_first_equals(self, workspace):
        (workspace / ".env").write_text("URL=http://example.com?a=1\n")
        result = load_env()
        assert result["URL"] == "http://example.com?a=1"

    def test_empty_env_file(self, workspace):
        (workspace / ".env").write_text("")
        result = load_env()
        assert result == {}


# ---------------------------------------------------------------------------
# write_env
# ---------------------------------------------------------------------------

class TestWriteEnv:
    def test_writes_key_value_pairs(self, workspace):
        write_env({"KEY": "val", "OTHER": "thing"})
        content = (workspace / ".env").read_text()
        assert "KEY=val" in content
        assert "OTHER=thing" in content

    def test_file_ends_with_newline(self, workspace):
        write_env({"A": "1"})
        content = (workspace / ".env").read_text()
        assert content.endswith("\n")

    def test_empty_dict_writes_newline(self, workspace):
        write_env({})
        content = (workspace / ".env").read_text()
        assert content == "\n"

    def test_roundtrip(self, workspace):
        env = {"X": "10", "Y": "20"}
        write_env(env)
        result = load_env()
        assert result["X"] == "10"
        assert result["Y"] == "20"


# ---------------------------------------------------------------------------
# set_var
# ---------------------------------------------------------------------------

class TestSetVar:
    def test_sets_new_variable(self, workspace):
        (workspace / ".env").write_text("EXISTING=yes\n")
        set_var("NEW_KEY", "new_value")
        result = load_env()
        assert result["NEW_KEY"] == "new_value"

    def test_updates_existing_variable(self, workspace):
        (workspace / ".env").write_text("KEY=old\n")
        set_var("KEY", "new")
        result = load_env()
        assert result["KEY"] == "new"

    def test_preserves_other_variables(self, workspace):
        (workspace / ".env").write_text("A=1\nB=2\n")
        set_var("A", "99")
        result = load_env()
        assert result["B"] == "2"

    def test_creates_env_file_if_missing(self, workspace):
        set_var("MYKEY", "myval")
        result = load_env()
        assert result["MYKEY"] == "myval"


# ---------------------------------------------------------------------------
# env:set command group — basic invocation
# ---------------------------------------------------------------------------

class TestEnvSetGroup:
    def test_help_text_shown(self, workspace):
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(env_set_group, ["--help"])
        assert result.exit_code == 0
        assert "env:set" in result.output or "Set environment" in result.output
