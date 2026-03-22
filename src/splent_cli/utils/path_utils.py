import os

# Base PathUtils (framework-level paths) lives in splent_framework.
# This module re-exports it and adds CLI-specific path helpers on top.
from splent_framework.utils.path_utils import (
    PathUtils as _BasePathUtils,
)  # noqa: F401


class PathUtils(_BasePathUtils):
    """PathUtils extended with SPLENT CLI-specific paths."""

    @staticmethod
    def get_splent_cli_dir():
        working_dir = _BasePathUtils.get_working_dir()
        return os.path.join(working_dir, "splent_cli", "src", "splent_cli")

    @staticmethod
    def get_splent_cli_templates_dir():
        return os.path.join(PathUtils.get_splent_cli_dir(), "templates")

    @staticmethod
    def get_commands_dir():
        return os.path.join(PathUtils.get_splent_cli_dir(), "commands")

    @staticmethod
    def get_commands_path():
        return os.path.abspath(PathUtils.get_commands_dir())

    @staticmethod
    def get_splent_framework_dir():
        working_dir = _BasePathUtils.get_working_dir()
        return os.path.join(working_dir, "splent_framework", "src", "splent_framework")

    @staticmethod
    def get_core_dir():
        return PathUtils.get_splent_framework_dir()
