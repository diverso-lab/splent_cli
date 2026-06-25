"""Tests for splent_cli.utils.proc subprocess helpers.

These cover the hardened behaviors: friendly translation of missing-tool,
non-zero-exit, and timeout failures into click.ClickException (no raw
tracebacks), plus require_tool / require_docker preflight checks.

All subprocess / PATH interactions are mocked at the boundary:
  * splent_cli.utils.proc.subprocess.run
  * splent_cli.utils.proc.shutil.which
No real docker / git / network is required.
"""
import subprocess

import click
import pytest

from splent_cli.utils import proc


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["x"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# --------------------------------------------------------------------------- #
# run()
# --------------------------------------------------------------------------- #
class TestRun:
    def test_missing_tool_raises_clickexception_naming_tool(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(proc.subprocess, "run", boom)
        with pytest.raises(click.ClickException) as exc:
            proc.run(["git", "status"])
        msg = str(exc.value)
        assert "git" in msg
        assert "PATH" in msg
        assert "Traceback" not in msg

    def test_missing_tool_uses_custom_tool_hint(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError()

        monkeypatch.setattr(proc.subprocess, "run", boom)
        with pytest.raises(click.ClickException) as exc:
            proc.run(["mysql"], tool_hint="brew install mysql-client")
        assert "brew install mysql-client" in str(exc.value)

    def test_nonzero_exit_with_check_raises_with_captured_stderr(self, monkeypatch):
        monkeypatch.setattr(
            proc.subprocess,
            "run",
            lambda *a, **k: _completed(returncode=2, stderr="fatal: not a git repo"),
        )
        with pytest.raises(click.ClickException) as exc:
            proc.run(["git", "log"], check=True, capture=True)
        msg = str(exc.value)
        assert "exit 2" in msg
        assert "fatal: not a git repo" in msg
        assert "CalledProcessError" not in msg
        assert "Traceback" not in msg

    def test_nonzero_exit_falls_back_to_stdout_when_no_stderr(self, monkeypatch):
        monkeypatch.setattr(
            proc.subprocess,
            "run",
            lambda *a, **k: _completed(returncode=1, stdout="boom on stdout", stderr=""),
        )
        with pytest.raises(click.ClickException) as exc:
            proc.run(["ruff"], check=True, capture=True)
        assert "boom on stdout" in str(exc.value)

    def test_nonzero_exit_without_check_returns_completedprocess(self, monkeypatch):
        cp = _completed(returncode=3, stdout="out", stderr="err")
        monkeypatch.setattr(proc.subprocess, "run", lambda *a, **k: cp)
        result = proc.run(["pytest"], check=False, capture=True)
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 3

    def test_success_returns_completedprocess_returncode_zero(self, monkeypatch):
        cp = _completed(returncode=0, stdout="hello")
        monkeypatch.setattr(proc.subprocess, "run", lambda *a, **k: cp)
        result = proc.run(["echo", "hi"])
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert result.stdout == "hello"

    def test_timeout_raises_clickexception(self, monkeypatch):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="git", timeout=5)

        monkeypatch.setattr(proc.subprocess, "run", boom)
        with pytest.raises(click.ClickException) as exc:
            proc.run(["git", "fetch"], timeout=5)
        msg = str(exc.value)
        assert "git" in msg
        assert "timed out" in msg
        assert "Traceback" not in msg

    def test_capture_shorthand_maps_to_capture_output(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return _completed(returncode=0)

        monkeypatch.setattr(proc.subprocess, "run", fake_run)
        proc.run(["echo"], capture=True)
        assert seen.get("capture_output") is True


# --------------------------------------------------------------------------- #
# require_tool()
# --------------------------------------------------------------------------- #
class TestRequireTool:
    def test_missing_tool_raises_with_hint(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: None)
        with pytest.raises(click.ClickException) as exc:
            proc.require_tool("git", "Install git from https://git-scm.com")
        msg = str(exc.value)
        assert "git" in msg
        assert "Install git from https://git-scm.com" in msg

    def test_missing_tool_without_hint_still_names_tool(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: None)
        with pytest.raises(click.ClickException) as exc:
            proc.require_tool("ruff")
        assert "ruff" in str(exc.value)

    def test_present_tool_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: "/usr/bin/git")
        assert proc.require_tool("git") is None


# --------------------------------------------------------------------------- #
# require_docker()
# --------------------------------------------------------------------------- #
class TestRequireDocker:
    def test_docker_missing_raises(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: None)
        with pytest.raises(click.ClickException) as exc:
            proc.require_docker()
        assert "docker" in str(exc.value).lower()

    def test_daemon_down_generic_says_not_running(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(
            proc.subprocess,
            "run",
            lambda *a, **k: _completed(
                returncode=1, stderr="Cannot connect to the Docker daemon"
            ),
        )
        with pytest.raises(click.ClickException) as exc:
            proc.require_docker()
        assert "not running" in str(exc.value).lower()

    def test_permission_denied_says_without_sudo(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(
            proc.subprocess,
            "run",
            lambda *a, **k: _completed(
                returncode=1,
                stderr="Got permission denied while trying to connect",
            ),
        )
        with pytest.raises(click.ClickException) as exc:
            proc.require_docker()
        assert "without sudo" in str(exc.value).lower()

    def test_timeout_raises_clickexception(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: "/usr/bin/docker")

        def boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="docker info", timeout=15)

        monkeypatch.setattr(proc.subprocess, "run", boom)
        with pytest.raises(click.ClickException) as exc:
            proc.require_docker()
        assert "timed out" in str(exc.value).lower()

    def test_healthy_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(proc.shutil, "which", lambda name: "/usr/bin/docker")
        monkeypatch.setattr(
            proc.subprocess, "run", lambda *a, **k: _completed(returncode=0)
        )
        assert proc.require_docker() is None
