"""
Tests for the product:run command.

product_run queries docker containers — we mock subprocess.run to control
what docker ps -q and docker inspect return.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from click.testing import CliRunner

from splent_cli.commands.product.product_run import product_runc


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _ps_result(ids=None):
    """Mock docker compose ps -q result."""
    stdout = "\n".join(ids or [])
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def _inspect_result(mounts=None):
    """Mock docker inspect mounts result."""
    text = " ".join(mounts or [])
    return MagicMock(returncode=0, stdout=text, stderr="")


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------

class TestFlagValidation:
    def test_rejects_both_dev_and_prod(self, runner, product_workspace):
        result = runner.invoke(product_runc, ["--dev", "--prod"])
        assert result.exit_code == 1
        assert "cannot" in result.output.lower() or "same time" in result.output.lower()

    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(product_runc, ["--dev"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# No containers running → run locally
# ---------------------------------------------------------------------------

class TestNoContainers:
    def test_runs_locally_when_no_containers(self, runner, product_workspace):
        def fake_run(cmd, **kwargs):
            if "ps" in cmd:
                return _ps_result([])  # no containers
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(product_runc, ["--dev"])
        assert result.exit_code == 0
        assert "locally" in result.output or "No containers" in result.output


# ---------------------------------------------------------------------------
# Container found → exec entrypoint
# ---------------------------------------------------------------------------

class TestContainerFound:
    def test_execs_in_container_with_workspace_mount(self, runner, product_workspace):
        call_cmds = []

        def fake_run(cmd, **kwargs):
            call_cmds.append(cmd)
            if "ps" in cmd:
                return _ps_result(["abc123"])
            if "inspect" in cmd:
                return _inspect_result(["/workspace"])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(product_runc, ["--dev"])

        assert result.exit_code == 0
        exec_calls = [c for c in call_cmds if "exec" in c]
        assert exec_calls, "Expected a docker exec call"
        assert "abc123" in exec_calls[0]

    def test_uses_first_container_when_no_workspace_mount(self, runner, product_workspace):
        """Falls back to container_ids[0] when no container has /workspace mount."""
        call_cmds = []

        def fake_run(cmd, **kwargs):
            call_cmds.append(cmd)
            if "ps" in cmd:
                return _ps_result(["def456"])
            if "inspect" in cmd:
                return _inspect_result(["/other"])  # not /workspace
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(product_runc, ["--dev"])

        assert result.exit_code == 0
        exec_calls = [c for c in call_cmds if "exec" in c]
        assert exec_calls
        assert "def456" in exec_calls[0]

    def test_execs_entrypoint_in_found_container(self, runner, product_workspace):
        call_cmds = []

        def fake_run(cmd, **kwargs):
            call_cmds.append(cmd)
            if "ps" in cmd:
                return _ps_result(["abc123def456"])
            if "inspect" in cmd:
                return _inspect_result(["/workspace"])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(product_runc, ["--dev"])

        assert result.exit_code == 0
        exec_calls = [c for c in call_cmds if "exec" in c]
        assert exec_calls, "Expected a docker exec call"
