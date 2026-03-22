"""
Tests for splent_cli.services.context

These functions have no external dependencies — just os.getenv and click.
Test them directly (not through a command) for clarity.
"""
import pytest
from pathlib import Path
from click.testing import CliRunner
import click

from splent_cli.services import context


# ---------------------------------------------------------------------------
# context.workspace()
# ---------------------------------------------------------------------------

class TestWorkspace:
    def test_default_is_workspace(self, monkeypatch):
        monkeypatch.delenv("WORKING_DIR", raising=False)
        assert context.workspace() == Path("/workspace")

    def test_reads_working_dir_env(self, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", "/custom/path")
        assert context.workspace() == Path("/custom/path")

    def test_returns_path_object(self, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", "/some/dir")
        assert isinstance(context.workspace(), Path)


# ---------------------------------------------------------------------------
# context.require_app()
# ---------------------------------------------------------------------------

class TestRequireApp:
    def test_returns_app_name(self, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "my_product")
        assert context.require_app() == "my_product"

    def test_exits_when_not_set(self, monkeypatch):
        monkeypatch.delenv("SPLENT_APP", raising=False)
        # require_app() calls raise SystemExit(1) — test it directly
        with pytest.raises(SystemExit) as exc_info:
            context.require_app()
        assert exc_info.value.code == 1

    def test_exit_message_shown(self, monkeypatch):
        """The error message is printed to stdout before exit."""
        monkeypatch.delenv("SPLENT_APP", raising=False)

        # Wrap it in a minimal Click command so CliRunner can capture output
        @click.command()
        def _cmd():
            context.require_app()

        result = CliRunner().invoke(_cmd, [])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# context.resolve_env()
# ---------------------------------------------------------------------------

class TestResolveEnv:
    def test_prod_flag_wins(self, monkeypatch):
        monkeypatch.setenv("SPLENT_ENV", "dev")
        assert context.resolve_env(env_dev=False, env_prod=True) == "prod"

    def test_dev_flag_wins(self, monkeypatch):
        monkeypatch.setenv("SPLENT_ENV", "prod")
        assert context.resolve_env(env_dev=True, env_prod=False) == "dev"

    def test_falls_back_to_splent_env(self, monkeypatch):
        monkeypatch.setenv("SPLENT_ENV", "prod")
        assert context.resolve_env(env_dev=False, env_prod=False) == "prod"

    def test_default_is_dev(self, monkeypatch):
        monkeypatch.delenv("SPLENT_ENV", raising=False)
        assert context.resolve_env(env_dev=False, env_prod=False) == "dev"

    def test_no_flags_no_env(self, monkeypatch):
        monkeypatch.delenv("SPLENT_ENV", raising=False)
        assert context.resolve_env() == "dev"
