"""
Tests for product:deploy error handling (hardened behaviors).

Focus:
  * When ``docker compose up`` fails, the captured output/error is dumped to the
    user (capture_output=True is set on the call, so the CalledProcessError
    carries .stderr/.stdout and the error lines are printed — not a bare
    "Deployment failed.").
  * When docker is missing, a clean ClickException message is shown (no
    traceback / no raw FileNotFoundError).

All subprocess / docker boundaries are mocked — no real docker / network.
"""

import subprocess
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from splent_cli.commands.product.product_deploy import product_deploy


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _deploy_workspace(tmp_path, monkeypatch):
    """Wire up a workspace where product:deploy has all build artifacts.

    Returns the docker_dir so tests can inspect/extend files.
    """
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")

    docker_dir = tmp_path / "test_app" / "docker"
    docker_dir.mkdir(parents=True)

    # Build artifacts required before deploy proceeds. No <SET> values so the
    # command never blocks on an interactive prompt.
    (docker_dir / ".env.deploy.example").write_text(
        "FLASK_APP=app\nMYSQL_DATABASE=splent\n"
    )
    (docker_dir / ".env.deploy").write_text("FLASK_APP=app\nMYSQL_DATABASE=splent\n")
    # A compose file with an app port so the access-URL branch has something to
    # read; the deploy fails before it gets there in the failure tests.
    (docker_dir / "docker-compose.deploy.yml").write_text(
        'services:\n  web:\n    image: test\n    ports:\n      - "8080:5000"\n'
    )
    return docker_dir


# ---------------------------------------------------------------------------
# Hardened: compose-up failure surfaces the captured error
# ---------------------------------------------------------------------------


class TestComposeUpFailureSurfacesError:
    def test_failure_dumps_captured_stderr(self, runner, tmp_path, monkeypatch):
        _deploy_workspace(tmp_path, monkeypatch)

        error = subprocess.CalledProcessError(1, ["docker", "compose", "up"])
        error.stderr = "Error response from daemon: pull access denied for test"
        error.stdout = ""

        with (
            patch("splent_cli.commands.product.product_deploy.require_docker"),
            patch(
                "splent_cli.commands.product.product_derive._extract_host_ports",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_derive._containers_using_port",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_deploy.subprocess.run",
                side_effect=error,
            ),
        ):
            result = runner.invoke(product_deploy, [])

        assert result.exit_code == 1
        # The bare "Deployment failed." header is fine, but the captured daemon
        # error MUST also reach the user — this is the hardened behavior.
        assert "Deployment failed." in result.output
        assert "pull access denied" in result.output
        # No raw traceback leaking through.
        assert "Traceback" not in result.output
        assert "CalledProcessError" not in result.output

    def test_failure_falls_back_to_stdout_when_no_stderr(
        self, runner, tmp_path, monkeypatch
    ):
        _deploy_workspace(tmp_path, monkeypatch)

        error = subprocess.CalledProcessError(1, ["docker", "compose", "up"])
        error.stderr = ""
        error.stdout = "service web failed to build"

        with (
            patch("splent_cli.commands.product.product_deploy.require_docker"),
            patch(
                "splent_cli.commands.product.product_derive._extract_host_ports",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_derive._containers_using_port",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_deploy.subprocess.run",
                side_effect=error,
            ),
        ):
            result = runner.invoke(product_deploy, [])

        assert result.exit_code == 1
        assert "service web failed to build" in result.output
        assert "Traceback" not in result.output

    def test_compose_up_uses_capture_output(self, runner, tmp_path, monkeypatch):
        """The up call must capture output, otherwise the error dump is empty."""
        _deploy_workspace(tmp_path, monkeypatch)

        error = subprocess.CalledProcessError(1, ["docker", "compose", "up"])
        error.stderr = "boom"
        error.stdout = ""
        captured_kwargs = {}

        def fake_run(cmd, *args, **kwargs):
            # Record kwargs of the compose-up invocation, then fail.
            if "up" in cmd:
                captured_kwargs.update(kwargs)
            raise error

        with (
            patch("splent_cli.commands.product.product_deploy.require_docker"),
            patch(
                "splent_cli.commands.product.product_derive._extract_host_ports",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_derive._containers_using_port",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_deploy.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            result = runner.invoke(product_deploy, [])

        assert result.exit_code == 1
        # capture_output (or stdout+stderr capture) must be requested so the
        # CalledProcessError carries the error text.
        assert captured_kwargs.get("capture_output") is True


# ---------------------------------------------------------------------------
# Hardened: docker missing -> clean message (no traceback)
# ---------------------------------------------------------------------------


class TestDockerMissing:
    def test_docker_missing_clean_message(self, runner, tmp_path, monkeypatch):
        _deploy_workspace(tmp_path, monkeypatch)

        # require_docker uses shutil.which via the proc helper; None => missing.
        with patch("splent_cli.utils.proc.shutil.which", return_value=None):
            result = runner.invoke(product_deploy, [])

        assert result.exit_code != 0
        out = result.output + result.stderr
        assert "docker" in out.lower()
        assert "Traceback" not in out
        assert "FileNotFoundError" not in out

    def test_docker_missing_on_down(self, runner, tmp_path, monkeypatch):
        docker_dir = _deploy_workspace(tmp_path, monkeypatch)
        # --down only needs the compose file to exist (it does).
        assert (docker_dir / "docker-compose.deploy.yml").exists()

        with patch("splent_cli.utils.proc.shutil.which", return_value=None):
            result = runner.invoke(product_deploy, ["--down"])

        assert result.exit_code != 0
        out = result.output + result.stderr
        assert "docker" in out.lower()
        assert "Traceback" not in out


# ---------------------------------------------------------------------------
# Down: missing compose file
# ---------------------------------------------------------------------------


class TestDownMissingComposeFile:
    def test_down_without_compose_exits_clean(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        (tmp_path / "test_app" / "docker").mkdir(parents=True)
        # No docker-compose.deploy.yml present.

        result = runner.invoke(product_deploy, ["--down"])
        assert result.exit_code == 1
        assert "docker-compose.deploy.yml not found" in result.output
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Missing build artifacts
# ---------------------------------------------------------------------------


class TestMissingBuildArtifacts:
    def test_requires_splent_app(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("SPLENT_APP", raising=False)
        result = runner.invoke(product_deploy, [])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output

    def test_missing_env_example_tells_user_to_build(
        self, runner, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        (tmp_path / "test_app" / "docker").mkdir(parents=True)
        # Neither .env.deploy.example nor compose present.

        result = runner.invoke(product_deploy, [])
        assert result.exit_code == 1
        assert ".env.deploy.example not found" in result.output
        assert "product:build" in result.output

    def test_missing_compose_tells_user_to_build(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        docker_dir = tmp_path / "test_app" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / ".env.deploy.example").write_text("FOO=bar\n")
        # compose file missing

        result = runner.invoke(product_deploy, [])
        assert result.exit_code == 1
        assert "docker-compose.deploy.yml not found" in result.output
        assert "product:build" in result.output


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_successful_deploy_healthy_app(self, runner, tmp_path, monkeypatch):
        _deploy_workspace(tmp_path, monkeypatch)

        def fake_run(cmd, *args, **kwargs):
            # compose up -> success; health-check exec -> HTTP 200
            if "exec" in cmd:
                return MagicMock(returncode=0, stdout="200", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("splent_cli.commands.product.product_deploy.require_docker"),
            patch(
                "splent_cli.commands.product.product_derive._extract_host_ports",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_derive._containers_using_port",
                return_value=[],
            ),
            patch(
                "splent_cli.commands.product.product_deploy.subprocess.run",
                side_effect=fake_run,
            ),
        ):
            result = runner.invoke(product_deploy, [])

        assert result.exit_code == 0
        assert "done." in result.output
        assert "http://localhost:8080" in result.output
        assert "Traceback" not in result.output

    def test_successful_down(self, runner, tmp_path, monkeypatch):
        _deploy_workspace(tmp_path, monkeypatch)

        with (
            patch("splent_cli.commands.product.product_deploy.require_docker"),
            patch(
                "splent_cli.commands.product.product_deploy.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="", stderr=""),
            ),
        ):
            result = runner.invoke(product_deploy, ["--down"])

        assert result.exit_code == 0
        assert "done." in result.output
        assert "Traceback" not in result.output
