"""Tests for product:up — Docker daemon check and failure propagation."""
import subprocess
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.product.product_up import product_up, _check_docker_running
import click
import pytest


class TestDockerDaemonCheck:
    def test_exits_when_docker_not_running(self, product_workspace):
        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.product.product_up.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 1
        assert "Docker" in result.output or "docker" in result.output

    def test_exits_with_helpful_message_when_docker_down(self, product_workspace):
        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.product.product_up.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
            result = runner.invoke(product_up, ["--dev"])
        assert "running" in result.output.lower() or "reachable" in result.output.lower()

    def test_passes_when_docker_running(self, product_workspace):
        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.product.product_up.subprocess.run") as mock_run:
            # docker info → ok, then docker compose up → ok
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(product_up, ["--dev"])
        # Should not fail with Docker error (may still fail on missing compose, that's ok)
        assert "Docker daemon is not running" not in result.output


class TestFeatureFailurePropagation:
    def test_product_not_launched_when_feature_fails(self, product_workspace):
        """If a feature service fails to start, the product itself must not be launched."""
        runner = CliRunner(mix_stderr=False)

        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            if "docker" == cmd[0] and "info" == cmd[1]:
                result.returncode = 0
                return result
            # First compose up (feature) → fail, second (product) → should never reach
            call_count["n"] += 1
            result.returncode = 1 if call_count["n"] == 1 else 0
            return result

        (product_workspace / "test_app" / "docker" / "docker-compose.dev.yml").write_text(
            "services:\n  web:\n    image: alpine\n"
        )

        with patch("splent_cli.commands.product.product_up.subprocess.run", side_effect=mock_run):
            result = runner.invoke(product_up, ["--dev"])

        assert result.exit_code == 1
        assert "✗" in result.output
        # Product was not launched (only 1 compose call, not 2)
        assert call_count["n"] == 1

    def test_exit_code_1_when_any_service_fails(self, product_workspace):
        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.product.product_up.subprocess.run") as mock_run:
            info_result = MagicMock(returncode=0)
            fail_result = MagicMock(returncode=1)
            mock_run.side_effect = [info_result, fail_result]
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 1
