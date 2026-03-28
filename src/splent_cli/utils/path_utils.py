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
        """Return the splent_cli package directory.

        In development (editable install), this is WORKING_DIR/splent_cli/src/splent_cli.
        In production (pip install from PyPI), this is wherever the package lives
        in site-packages.  We detect which case applies by checking if the
        workspace path exists; if not, we fall back to the installed package location.
        """
        working_dir = _BasePathUtils.get_working_dir()
        dev_path = os.path.join(working_dir, "splent_cli", "src", "splent_cli")
        if os.path.isdir(dev_path):
            return dev_path
        # Fallback: resolve from the installed package itself
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
