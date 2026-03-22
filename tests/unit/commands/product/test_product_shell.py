"""
Tests for the product:shell command.
"""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_shell import product_shell, _find_main_container


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _find_main_container() helper
# ---------------------------------------------------------------------------

class TestFindMainContainer:
    def test_returns_container_with_workspace_mount(self, tmp_path):
        def fake_run(cmd, **kwargs):
            if "ps" in cmd:
                return MagicMock(returncode=0, stdout="abc123\n")
            if "inspect" in cmd:
                return MagicMock(returncode=0, stdout="/workspace /other")
        with patch("subprocess.run", side_effect=fake_run):
            result = _find_main_container("proj", "file.yml", str(tmp_path))
        assert result == "abc123"

    def test_falls_back_to_first_container(self, tmp_path):
        def fake_run(cmd, **kwargs):
            if "ps" in cmd:
                return MagicMock(returncode=0, stdout="abc123\ndef456\n")
            if "inspect" in cmd:
                return MagicMock(returncode=0, stdout="/other")
        with patch("subprocess.run", side_effect=fake_run):
            result = _find_main_container("proj", "file.yml", str(tmp_path))
        assert result == "abc123"

    def test_returns_none_when_no_containers(self, tmp_path):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            result = _find_main_container("proj", "file.yml", str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------

class TestFlagValidation:
    def test_rejects_both_dev_and_prod(self, runner, product_workspace):
        result = runner.invoke(product_shell, ["--dev", "--prod"])
        assert result.exit_code == 1
        assert "Cannot specify both" in result.output

    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(product_shell, ["--dev"])
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

        result = runner.invoke(product_shell, ["--dev"])
        assert result.exit_code == 1
        assert "No docker-compose" in result.output


# ---------------------------------------------------------------------------
# No running containers
# ---------------------------------------------------------------------------

class TestNoRunningContainers:
    def test_exits_when_no_containers(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="")):
            result = runner.invoke(product_shell, ["--dev"])
        assert result.exit_code == 1
        assert "No running containers" in result.output


# ---------------------------------------------------------------------------
# --service flag: exec by service name
# ---------------------------------------------------------------------------

class TestServiceFlag:
    def test_service_uses_compose_exec(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_shell, ["--dev", "--service", "web"])
        calls = [c[0][0] for c in mock_run.call_args_list]
        exec_calls = [c for c in calls if "exec" in c]
        assert exec_calls
        assert "web" in exec_calls[0]

    def test_service_with_cmd_appends_sh_c(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(product_shell, ["--dev", "--service", "web", "--cmd", "ls"])
        calls = [c[0][0] for c in mock_run.call_args_list]
        exec_calls = [c for c in calls if "exec" in c]
        assert exec_calls
        assert "ls" in exec_calls[0]


# ---------------------------------------------------------------------------
# Container found → exec
# ---------------------------------------------------------------------------

class TestContainerFound:
    def test_opens_shell_in_container(self, runner, product_workspace):
        call_cmds = []

        def fake_run(cmd, **kwargs):
            call_cmds.append(cmd)
            if "ps" in cmd:
                return MagicMock(returncode=0, stdout="abc123\n")
            if "inspect" in cmd:
                return MagicMock(returncode=0, stdout="/workspace")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(product_shell, ["--dev"])

        assert result.exit_code == 0
        exec_calls = [c for c in call_cmds if "exec" in c]
        assert exec_calls
        assert "abc123" in exec_calls[0]

    def test_bash_not_found_falls_back_to_sh(self, runner, product_workspace):
        call_cmds = []

        def fake_run(cmd, **kwargs):
            call_cmds.append(cmd)
            if "ps" in cmd:
                return MagicMock(returncode=0, stdout="abc123\n")
            if "inspect" in cmd:
                return MagicMock(returncode=0, stdout="/workspace")
            if "exec" in cmd:
                return MagicMock(returncode=126)  # bash not found
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(product_shell, ["--dev"])

        exec_calls = [c for c in call_cmds if "exec" in c]
        # Should have tried bash and then sh
        assert len(exec_calls) >= 2
        assert exec_calls[-1][-1] == "sh"
