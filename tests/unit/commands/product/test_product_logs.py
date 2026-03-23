"""
Tests for the product:logs command.
"""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_logs import product_logs


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------

class TestFlagValidation:
    def test_rejects_both_dev_and_prod(self, runner, product_workspace):
        result = runner.invoke(product_logs, ["--dev", "--prod"])
        assert result.exit_code == 1
        assert "Cannot specify both" in result.output

    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(product_logs, ["--dev"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# No compose file
# ---------------------------------------------------------------------------

class TestNoComposeFile:
    def test_exits_when_no_compose_file(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        (tmp_path / "test_app" / "docker").mkdir(parents=True)

        result = runner.invoke(product_logs, ["--dev"])
        assert result.exit_code == 1
        assert "No docker-compose file" in result.output


# ---------------------------------------------------------------------------
# Successful invocation
# ---------------------------------------------------------------------------

class TestSuccessfulInvocation:
    def test_runs_without_error(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = runner.invoke(product_logs, ["--dev"])
        assert result.exit_code == 0

    def test_default_tail_is_50(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_logs, ["--dev"])
        args = mock_run.call_args[0][0]
        assert "--tail" in args
        assert "50" in args

    def test_custom_tail(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_logs, ["--dev", "--tail", "100"])
        args = mock_run.call_args[0][0]
        assert "100" in args

    def test_no_follow_flag_omits_f(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_logs, ["--dev", "--no-follow"])
        args = mock_run.call_args[0][0]
        # The follow -f comes after "logs"; the compose file -f comes before it
        logs_idx = args.index("logs")
        assert "-f" not in args[logs_idx:]

    def test_follow_by_default_adds_f(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_logs, ["--dev"])
        args = mock_run.call_args[0][0]
        logs_idx = args.index("logs")
        assert "-f" in args[logs_idx:]

    def test_service_filter_appended(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_logs, ["--dev", "--service", "web"])
        args = mock_run.call_args[0][0]
        assert "web" in args

    def test_uses_correct_compose_file(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_logs, ["--prod"])
        args = mock_run.call_args[0][0]
        assert "docker-compose.prod.yml" in " ".join(args)


# ---------------------------------------------------------------------------
# KeyboardInterrupt is silently swallowed
# ---------------------------------------------------------------------------

class TestKeyboardInterrupt:
    def test_keyboard_interrupt_exits_cleanly(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=KeyboardInterrupt):
            result = runner.invoke(product_logs, ["--dev"])
        assert result.exit_code == 0
