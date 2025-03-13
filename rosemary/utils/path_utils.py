import os
import importlib.util
from dotenv import load_dotenv
from flasky.core.configuration.configuration import uploads_folder_name

load_dotenv()


class PathUtils:

    @staticmethod
    def get_working_dir():
        return os.getenv("WORKING_DIR", "")

    @staticmethod
    def get_app_dir():
        working_dir = PathUtils.get_working_dir()
        splendid = os.getenv("SPLENDID", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        if splendid:
            # Git submodules mode
            return os.path.join(working_dir, "flasky_app", "app")

        # PyPi mode
        package = importlib.util.find_spec("app")
        if package and package.origin:
            return os.path.dirname(package.origin)

        raise FileNotFoundError(
            "Could not find 'app'. Check the installation."
        )

    @staticmethod
    def get_modules_dir():
        return os.path.join(PathUtils.get_app_dir(), "modules")

    @staticmethod
    def get_rosemary_dir():
        splendid = os.getenv("SPLENDID", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        base_dir = os.getcwd()
        if splendid:
            return os.path.join(base_dir, "rosemary_cli", "rosemary")

        return os.path.join(base_dir, "rosemary")

    @staticmethod
    def get_rosemary_templates_dir():
        return os.path.join(PathUtils.get_rosemary_dir(), "templates")

    @staticmethod
    def get_commands_dir():
        return os.path.join(PathUtils.get_rosemary_dir(), "commands")

    @staticmethod
    def get_commands_path():
        return os.path.abspath(PathUtils.get_commands_dir())

    @staticmethod
    def get_flasky_dir():
        working_dir = PathUtils.get_working_dir()
        splendid = os.getenv("SPLENDID", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        if splendid:
            # Git submodules mode
            return os.path.join(working_dir, "flasky_framework", "flasky")

        # PyPi mode
        package = importlib.util.find_spec("flasky")
        if package and package.origin:
            return os.path.dirname(package.origin)

    @staticmethod
    def get_core_dir():
        return os.path.join(PathUtils.get_flasky_dir(), "core")

    @staticmethod
    def get_env_dir():
        return os.path.join(PathUtils.get_working_dir(), ".env")

    @staticmethod
    def get_app_log_dir():
        return os.path.join(PathUtils.get_working_dir(), "app.log")

    def get_uploads_dir():
        working_dir = PathUtils.get_working_dir()
        splendid = os.getenv("SPLENDID", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        if splendid:
            # Git submodules mode
            return os.path.join(
                working_dir, "flasky_app", uploads_folder_name()
            )

        # PyPi mode
        package = importlib.util.find_spec("app")
        if package and package.origin:
            return os.path.join(
                os.path.dirname(package.origin), uploads_folder_name()
            )
