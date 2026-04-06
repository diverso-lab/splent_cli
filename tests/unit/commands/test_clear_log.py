"""
Tests for the clear:log command.
"""
import pytest
from unittest.mock import patch
from click.testing import CliRunner

from splent_cli.commands.clear.clear_log import clear_log


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


class TestClearLog:
    def test_removes_existing_log(self, runner, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("old log content")

        with patch("splent_cli.commands.clear.clear_log.PathUtils.get_app_log_dir", return_value=str(log_file)):
            result = runner.invoke(clear_log, [])

        assert result.exit_code == 0
        assert "successfully cleared" in result.output
        assert not log_file.exists()

    def test_shows_warning_when_log_missing(self, runner, tmp_path):
        missing = tmp_path / "app.log"

        with patch("splent_cli.commands.clear.clear_log.PathUtils.get_app_log_dir", return_value=str(missing)):
            result = runner.invoke(clear_log, [])

        assert result.exit_code == 0
        assert "does not exist" in result.output

    def test_shows_error_on_permission_failure(self, runner, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("content")

        def boom(path):
            raise PermissionError("denied")

        with patch("splent_cli.commands.clear.clear_log.PathUtils.get_app_log_dir", return_value=str(log_file)):
            with patch("os.remove", side_effect=boom):
                result = runner.invoke(clear_log, [])

        assert result.exit_code == 0
        assert "Error" in result.output
