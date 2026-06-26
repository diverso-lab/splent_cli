"""Tests for product:up — Docker daemon check and failure propagation."""

from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.product.product_up import product_up, _check_docker_running


class TestDockerDaemonCheck:
    """The product:up safety guarantee: abort with a clear message when the
    Docker daemon is unreachable.

    The daemon probe lives in the shared ``splent_cli.utils.proc.require_docker``
    helper (re-exposed here via the thin ``_check_docker_running`` wrapper). Its
    ``docker info`` call goes through the ``subprocess`` module singleton, so a
    failing ``subprocess.run`` triggers the abort. The message is raised as a
    click.ClickException which prints to *stderr*.
    """

    def test_exits_when_docker_not_running(self, product_workspace):
        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.product.product_up.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 1
        assert "Docker" in result.stderr or "docker" in result.stderr

    def test_exits_with_helpful_message_when_docker_down(self, product_workspace):
        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.product.product_up.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            result = runner.invoke(product_up, ["--dev"])
        assert (
            "running" in result.stderr.lower() or "reachable" in result.stderr.lower()
        )

    def test_check_docker_running_delegates_to_require_docker(self):
        """_check_docker_running is a thin wrapper over the shared require_docker."""
        with patch("splent_cli.commands.product.product_up.require_docker") as mock_req:
            _check_docker_running()
        mock_req.assert_called_once_with()

    def test_passes_when_docker_running(self, product_workspace):
        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.product.product_up.subprocess.run") as mock_run:
            # docker info → ok, then docker compose up → ok
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(product_up, ["--dev"])
        # Should not fail with Docker error (may still fail on missing compose, that's ok)
        assert "Docker daemon is not running" not in result.output
        assert "Docker daemon is not running" not in result.stderr


class TestFeatureFailurePropagation:
    def test_product_not_launched_when_feature_fails(self, product_workspace):
        """If a feature service fails to start, the product itself must not be launched."""
        runner = CliRunner(mix_stderr=False)

        call_count = {"n": 0}

        # ``docker info`` is the daemon probe (require_docker); everything else is
        # a ``docker compose up``. Both go through the same ``subprocess.run``
        # singleton, so one side_effect handles them and keeps the test
        # independent of whether a real Docker daemon is reachable.
        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "info":
                result.returncode = 0
                return result
            # First compose up (feature) → fail, second (product) → should never reach
            call_count["n"] += 1
            result.returncode = 1 if call_count["n"] == 1 else 0
            return result

        (
            product_workspace / "test_app" / "docker" / "docker-compose.dev.yml"
        ).write_text("services:\n  web:\n    image: alpine\n")

        with patch(
            "splent_cli.commands.product.product_up.subprocess.run",
            side_effect=mock_run,
        ):
            result = runner.invoke(product_up, ["--dev"])

        assert result.exit_code == 1
        assert "✗" in result.output
        # Product was not launched (only 1 compose call, not 2)
        assert call_count["n"] == 1

    def test_exit_code_1_when_any_service_fails(self, product_workspace):
        runner = CliRunner(mix_stderr=False)

        # ``docker info`` (daemon probe) → ok; the compose up → fail.
        def mock_run(cmd, **kwargs):
            if len(cmd) > 1 and cmd[0] == "docker" and cmd[1] == "info":
                return MagicMock(returncode=0)
            return MagicMock(returncode=1)

        with patch(
            "splent_cli.commands.product.product_up.subprocess.run",
            side_effect=mock_run,
        ):
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 1
