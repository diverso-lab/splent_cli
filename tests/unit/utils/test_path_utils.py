"""
Tests for splent_cli.utils.path_utils (the CLI shim over splent_framework PathUtils).

These methods are pure path compositions — no filesystem access, just string joins.
"""
import pytest
from splent_cli.utils.path_utils import PathUtils


@pytest.fixture(autouse=True)
def set_working_dir(monkeypatch):
    monkeypatch.setenv("WORKING_DIR", "/workspace")


class TestGetSplentCliDir:
    def test_returns_expected_path(self):
        assert PathUtils.get_splent_cli_dir() == "/workspace/splent_cli/src/splent_cli"

    def test_changes_with_working_dir(self, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", "/custom")
        assert PathUtils.get_splent_cli_dir() == "/custom/splent_cli/src/splent_cli"


class TestGetSplentCliTemplatesDir:
    def test_contains_templates(self):
        result = PathUtils.get_splent_cli_templates_dir()
        assert result.endswith("/templates")
        assert "splent_cli" in result

    def test_is_under_cli_dir(self):
        cli_dir = PathUtils.get_splent_cli_dir()
        templates_dir = PathUtils.get_splent_cli_templates_dir()
        assert templates_dir.startswith(cli_dir)


class TestGetCommandsDir:
    def test_contains_commands(self):
        result = PathUtils.get_commands_dir()
        assert result.endswith("/commands")
        assert "splent_cli" in result


class TestGetCommandsPath:
    def test_is_absolute(self):
        result = PathUtils.get_commands_path()
        assert result.startswith("/")

    def test_same_as_commands_dir(self):
        # get_commands_path() is os.path.abspath of get_commands_dir() — same on absolute paths
        import os
        assert PathUtils.get_commands_path() == os.path.abspath(PathUtils.get_commands_dir())


class TestGetSplentFrameworkDir:
    def test_returns_expected_path(self):
        result = PathUtils.get_splent_framework_dir()
        assert result == "/workspace/splent_framework/src/splent_framework"

    def test_changes_with_working_dir(self, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", "/custom")
        assert PathUtils.get_splent_framework_dir() == "/custom/splent_framework/src/splent_framework"


class TestGetCoreDir:
    def test_same_as_framework_dir(self):
        assert PathUtils.get_core_dir() == PathUtils.get_splent_framework_dir()
