"""
Tests for the check:docker command.

All subprocess.run calls are mocked — no real Docker needed.
"""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.check.check_docker import check_docker, _run, _ok, _fail, _warn


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _run() helper
# ---------------------------------------------------------------------------

class TestRunHelper:
    def test_returns_returncode_stdout_stderr(self):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="v1.0\n", stderr="")):
            rc, out, err = _run(["docker", "--version"])
        assert rc == 0
        assert out == "v1.0"

    def test_command_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            rc, out, err = _run(["nonexistent"])
        assert rc == 1
        assert "not found" in err

    def test_timeout_expired(self):
        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            rc, out, err = _run(["slow_cmd"])
        assert rc == 1
        assert "timed out" in err


# ---------------------------------------------------------------------------
# Output formatting helpers
# ---------------------------------------------------------------------------

class TestFormatHelpers:
    def test_ok_contains_checkmark(self):
        result = _ok("all good")
        assert "✔" in result
        assert "all good" in result

    def test_fail_contains_cross(self):
        result = _fail("broken")
        assert "✖" in result

    def test_warn_contains_warning(self):
        result = _warn("maybe")
        assert "⚠" in result


# ---------------------------------------------------------------------------
# All checks pass
# ---------------------------------------------------------------------------

class TestAllPass:
    def test_exits_0_when_all_pass(self, runner):
        def fake_run(cmd, **kwargs):
            return MagicMock(returncode=0, stdout="Docker version 24.0\n", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(check_docker, [])
        assert result.exit_code == 0
        assert "OK" in result.output

    def test_shows_docker_version(self, runner):
        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                return MagicMock(returncode=0, stdout="Docker version 24.0", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(check_docker, [])
        assert "Docker version 24.0" in result.output


# ---------------------------------------------------------------------------
# Docker not installed
# ---------------------------------------------------------------------------

class TestDockerNotInstalled:
    def test_exits_1_when_docker_missing(self, runner):
        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(check_docker, [])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# Legacy docker-compose fallback
# ---------------------------------------------------------------------------

class TestLegacyComposeFallback:
    def test_warn_on_legacy_compose(self, runner):
        def fake_run(cmd, **kwargs):
            if cmd == ["docker", "compose", "version"]:
                return MagicMock(returncode=1, stdout="", stderr="")
            if cmd == ["docker-compose", "--version"]:
                return MagicMock(returncode=0, stdout="docker-compose 1.29", stderr="")
            return MagicMock(returncode=0, stdout="out", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(check_docker, [])
        assert "Legacy" in result.output or "docker-compose" in result.output


# ---------------------------------------------------------------------------
# Docker daemon not running
# ---------------------------------------------------------------------------

class TestDaemonNotRunning:
    def test_exits_1_when_daemon_down(self, runner):
        def fake_run(cmd, **kwargs):
            if cmd == ["docker", "info"]:
                return MagicMock(returncode=1, stdout="", stderr="Cannot connect to Docker daemon")
            return MagicMock(returncode=0, stdout="output", stderr="")

        with patch("subprocess.run", side_effect=fake_run):
            result = runner.invoke(check_docker, [])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()
