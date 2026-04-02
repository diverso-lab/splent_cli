"""
Tests for the product:status command.

Pattern: mock subprocess.run to simulate docker compose ps output.
Use product_workspace fixture for a real filesystem + env var setup.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_containers import product_docker, _status_color


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _mock_run(containers: list[dict], returncode: int = 0):
    """Build a mock subprocess.run return value that looks like `docker compose ps --format json`."""
    stdout = "\n".join(json.dumps(c) for c in containers)
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------

class TestFlagValidation:
    def test_rejects_both_dev_and_prod(self, runner, product_workspace):
        result = runner.invoke(product_docker, ["--dev", "--prod"])
        assert result.exit_code == 1
        assert "Cannot specify both" in result.output

    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# No compose file
# ---------------------------------------------------------------------------

class TestNoComposeFile:
    def test_shows_no_containers_when_no_compose_file(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        (tmp_path / "test_app" / "docker").mkdir(parents=True)  # docker dir but no compose file

        result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "No containers" in result.output or "Is it up?" in result.output


# ---------------------------------------------------------------------------
# Successful docker compose ps output
# ---------------------------------------------------------------------------

CONTAINERS = [
    {"Service": "web", "State": "running", "Publishers": [{"PublishedPort": 8080, "TargetPort": 8000}]},
    {"Service": "db",  "State": "running", "Publishers": [{"PublishedPort": 5432, "TargetPort": 5432}]},
]


class TestRunningContainers:
    def test_shows_service_names(self, runner, product_workspace):
        with patch("subprocess.run", return_value=_mock_run(CONTAINERS)):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "db" in result.output

    def test_shows_container_state(self, runner, product_workspace):
        with patch("subprocess.run", return_value=_mock_run(CONTAINERS)):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "running" in result.output.lower()

    def test_shows_port_mapping(self, runner, product_workspace):
        with patch("subprocess.run", return_value=_mock_run(CONTAINERS)):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "8080" in result.output

    def test_uses_prod_env(self, runner, product_workspace):
        with patch("subprocess.run", return_value=_mock_run(CONTAINERS)) as mock_run:
            result = runner.invoke(product_docker, ["--prod"])
        assert result.exit_code == 0
        # Verify the compose file used was the prod one
        call_args = mock_run.call_args[0][0]
        assert "docker-compose.prod.yml" in " ".join(call_args)


# ---------------------------------------------------------------------------
# No containers running
# ---------------------------------------------------------------------------

class TestNoContainers:
    def test_empty_output_shows_info(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "No containers" in result.output or "Is it up?" in result.output


# ---------------------------------------------------------------------------
# docker compose ps failure
# ---------------------------------------------------------------------------

class TestDockerFailure:
    def test_shows_no_containers_on_docker_error(self, runner, product_workspace):
        with patch("subprocess.run", return_value=MagicMock(
            returncode=1, stdout="", stderr="Cannot connect to Docker daemon"
        )):
            result = runner.invoke(product_docker, ["--dev"])
        # Docker errors result in empty container list, not a hard failure
        assert result.exit_code == 0
        assert "No containers" in result.output or "Is it up?" in result.output


# ---------------------------------------------------------------------------
# Exited / unhealthy containers
# ---------------------------------------------------------------------------

class TestContainerStates:
    def test_exited_container(self, runner, product_workspace):
        containers = [{"Service": "worker", "State": "exited", "Publishers": []}]
        with patch("subprocess.run", return_value=_mock_run(containers)):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "worker" in result.output
        assert "exited" in result.output.lower()


# ---------------------------------------------------------------------------
# _status_color() — all branches (lines 13-19)
# ---------------------------------------------------------------------------

# click.style() wraps text in ANSI escape codes, not the color name as a string.
_GREEN  = "\x1b[32m"
_RED    = "\x1b[31m"
_YELLOW = "\x1b[33m"


class TestStatusColor:
    """Test the private helper directly — no CLI needed."""

    def test_running(self):
        assert _GREEN in _status_color("running")

    def test_exited(self):
        assert _RED in _status_color("exited")

    def test_stopped(self):
        assert _RED in _status_color("stopped")

    def test_starting(self):
        assert _YELLOW in _status_color("starting")

    def test_restarting(self):
        assert _YELLOW in _status_color("restarting")

    def test_unhealthy(self):
        assert _RED in _status_color("unhealthy")

    def test_healthy(self):
        assert _GREEN in _status_color("healthy")

    def test_unknown_state_returned_as_is(self):
        result = _status_color("paused")
        assert result == "paused"

    def test_case_insensitive(self):
        assert _GREEN in _status_color("Running")
        assert _RED in _status_color("EXITED")


# ---------------------------------------------------------------------------
# JSON parsing edge cases (lines 68, 72-73, 76-77)
# ---------------------------------------------------------------------------

class TestJsonParsing:
    def test_skips_empty_lines_in_output(self, runner, product_workspace):
        """Blank lines between JSON objects must be silently ignored (line 68)."""
        container = {"Service": "web", "State": "running", "Publishers": []}
        stdout_with_blanks = f"\n{json.dumps(container)}\n\n"
        with patch("subprocess.run", return_value=MagicMock(
            returncode=0, stdout=stdout_with_blanks, stderr=""
        )):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "web" in result.output

    def test_skips_malformed_json_lines(self, runner, product_workspace):
        """Malformed JSON lines must be silently skipped (lines 72-73)."""
        container = {"Service": "db", "State": "running", "Publishers": []}
        stdout_mixed = f"not-json\n{json.dumps(container)}\nalso-not-json\n"
        with patch("subprocess.run", return_value=MagicMock(
            returncode=0, stdout=stdout_mixed, stderr=""
        )):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "db" in result.output

    def test_no_containers_after_parse(self, runner, product_workspace):
        """Output has content but every line is malformed → 'No containers' message (lines 76-77)."""
        with patch("subprocess.run", return_value=MagicMock(
            returncode=0, stdout="not-json\nalso-not-json\n", stderr=""
        )):
            result = runner.invoke(product_docker, ["--dev"])
        assert result.exit_code == 0
        assert "No containers" in result.output
