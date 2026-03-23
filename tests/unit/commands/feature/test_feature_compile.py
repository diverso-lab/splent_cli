"""
Tests for feature_compile command helpers.

Focus: _is_inside_container() and the container/direct branching in
_compile_in_container(). Full integration (docker exec) is not testable
in unit tests — those paths require a running container.
"""

import subprocess
import pytest
from unittest.mock import MagicMock, patch
from splent_cli.commands.feature_compile import _is_inside_container, _compile_in_container


# ---------------------------------------------------------------------------
# _is_inside_container
# ---------------------------------------------------------------------------

class TestIsInsideContainer:
    def test_returns_true_when_dockerenv_exists(self, tmp_path, monkeypatch):
        dockerenv = tmp_path / ".dockerenv"
        dockerenv.touch()
        with patch("splent_cli.commands.feature_compile.os.path.exists", return_value=True):
            assert _is_inside_container() is True

    def test_returns_false_when_dockerenv_absent(self):
        with patch("splent_cli.commands.feature_compile.os.path.exists", return_value=False):
            assert _is_inside_container() is False


# ---------------------------------------------------------------------------
# _compile_in_container — direct mode (no container_id)
# ---------------------------------------------------------------------------

class TestCompileInContainerDirect:
    """When container_id is None the command runs webpack directly."""

    def _call(self, watch=False, production=False, extra_patch=None):
        with patch("splent_cli.commands.feature_compile.os.path.exists", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("subprocess.Popen") as mock_popen:
            _compile_in_container(
                container_id=None,
                feature="splent_io/splent_feature_auth@v1.0.0",
                watch=watch,
                production=production,
                workspace="/workspace",
                product="my_app",
            )
            return mock_run, mock_popen

    def test_uses_bash_directly_not_docker_exec(self):
        mock_run, _ = self._call()
        cmd = mock_run.call_args[0][0]
        assert "docker" not in cmd
        assert cmd[0] == "bash"

    def test_watch_mode_uses_popen(self):
        _, mock_popen = self._call(watch=True)
        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        assert "docker" not in cmd

    def test_webpack_watch_flag_added_in_dev_mode(self):
        _, mock_popen = self._call(watch=True, production=False)
        cmd = mock_popen.call_args[0][0]
        shell_cmd = cmd[2]
        assert "--watch" in shell_cmd

    def test_webpack_watch_flag_not_added_in_production(self):
        _, mock_popen = self._call(watch=True, production=True)
        cmd = mock_popen.call_args[0][0]
        shell_cmd = cmd[2]
        assert "--watch" not in shell_cmd


# ---------------------------------------------------------------------------
# _compile_in_container — docker exec mode (container_id present)
# ---------------------------------------------------------------------------

class TestCompileInContainerViaDocker:
    def test_uses_docker_exec_when_container_id_given(self):
        with patch("splent_cli.commands.feature_compile.os.path.exists", return_value=True), \
             patch("subprocess.run") as mock_run:
            _compile_in_container(
                container_id="abc123",
                feature="splent_io/splent_feature_auth@v1.0.0",
                watch=False,
                production=False,
                workspace="/workspace",
                product="my_app",
            )
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "docker"
        assert "exec" in cmd
        assert "abc123" in cmd

    def test_skips_when_no_webpack_config(self):
        with patch("splent_cli.commands.feature_compile.os.path.exists", return_value=False), \
             patch("subprocess.run") as mock_run:
            _compile_in_container(
                container_id="abc123",
                feature="splent_io/splent_feature_auth@v1.0.0",
                watch=False,
                production=False,
                workspace="/workspace",
                product="my_app",
            )
        mock_run.assert_not_called()
