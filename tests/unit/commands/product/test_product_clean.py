"""
Tests for the product:clean command.
"""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_clean import product_clean


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _success_run(*args, **kwargs):
    return MagicMock(returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------

class TestFlagValidation:
    def test_rejects_both_dev_and_prod(self, runner, product_workspace):
        result = runner.invoke(product_clean, ["--dev", "--prod", "--yes"])
        assert result.exit_code == 1
        assert "Cannot specify both" in result.output

    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------

class TestConfirmation:
    def test_yes_flag_skips_confirmation(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert result.exit_code == 0
        assert "fully cleaned" in result.output

    def test_cancel_at_prompt_exits(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_clean, ["--dev"], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output

    def test_confirm_at_prompt_proceeds(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_clean, ["--dev"], input="y\n")
        assert result.exit_code == 0
        assert "fully cleaned" in result.output


# ---------------------------------------------------------------------------
# Subprocess calls
# ---------------------------------------------------------------------------

class TestSubprocessCalls:
    def test_calls_db_reset(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_clean, ["--dev", "--yes"])
        cmds = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("db:reset" in cmd for cmd in cmds)

    def test_calls_clear_uploads(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_clean, ["--dev", "--yes"])
        cmds = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("clear:uploads" in cmd for cmd in cmds)

    def test_calls_clear_log(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_clean, ["--dev", "--yes"])
        cmds = [" ".join(c[0][0]) for c in mock_run.call_args_list]
        assert any("clear:log" in cmd for cmd in cmds)

    def test_db_reset_failure_shows_warning(self, runner, product_workspace):
        def mixed_run(cmd, **kwargs):
            if "db:reset" in cmd:
                return MagicMock(returncode=1)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=mixed_run):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert "⚠️" in result.output or "errors" in result.output.lower()


# ---------------------------------------------------------------------------
# Environment selection
# ---------------------------------------------------------------------------

class TestEnvironmentSelection:
    def test_defaults_to_dev(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_clean, ["--yes"])
        assert result.exit_code == 0
        assert "dev" in result.output or "test_app" in result.output

    def test_prod_env_shown_in_output(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_clean, ["--prod", "--yes"])
        assert "prod" in result.output
