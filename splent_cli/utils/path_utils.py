import os
import importlib.util
from dotenv import load_dotenv
from splent_framework.core.configuration.configuration import uploads_folder_name

load_dotenv()


class PathUtils:

    @staticmethod
    def get_working_dir():
        return os.getenv("WORKING_DIR", "")

    @staticmethod
    def get_app_dir():
        working_dir = PathUtils.get_working_dir()
        SPLENT = os.getenv("SPLENT", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        if SPLENT:
            # Git submodules mode
            return os.path.join(working_dir, "splent_app", "splent_app")

        # PyPi mode
        package = importlib.util.find_spec("splent_app")
        if package and package.origin:
            return os.path.dirname(package.origin)

        raise FileNotFoundError(
            "Could not find 'splent_app'. Check the installation."
        )

    @staticmethod
    def get_modules_dir():
        return os.path.join(PathUtils.get_app_dir(), "modules")

    @staticmethod
    def get_splent_cli_dir():
        SPLENT = os.getenv("SPLENT", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        base_dir = os.getcwd()
        if SPLENT:
            return os.path.join(base_dir, "splent_cli", "splent_cli")

        return os.path.join(base_dir, "splent_cli")

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
        working_dir = PathUtils.get_working_dir()
        SPLENT = os.getenv("SPLENT", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        if SPLENT:
            # Git submodules mode
            return os.path.join(working_dir, "splent_framework", "splent_framework")

        # PyPi mode
        package = importlib.util.find_spec("splent_framework")
        if package and package.origin:
            return os.path.dirname(package.origin)

    @staticmethod
    def get_core_dir():
        return os.path.join(PathUtils.get_splent_framework_dir(), "core")

    @staticmethod
    def get_env_dir():
        return os.path.join(PathUtils.get_working_dir(), ".env")

    @staticmethod
    def get_app_log_dir():
        return os.path.join(PathUtils.get_working_dir(), "app.log")

    def get_uploads_dir():
        working_dir = PathUtils.get_working_dir()
        SPLENT = os.getenv("SPLENT", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        if SPLENT:
            # Git submodules mode
            return os.path.join(
                working_dir, "splent_app", uploads_folder_name()
            )

        # PyPi mode
        package = importlib.util.find_spec("splent_app")
        if package and package.origin:
            return os.path.join(
                os.path.dirname(package.origin), uploads_folder_name()
            )
