import os
import sys
import subprocess
import tomllib
import importlib
from dotenv import load_dotenv
from importlib.metadata import distributions

from splent_cli.utils.path_utils import PathUtils

load_dotenv()

_app_instance = None
_db_instance = None
_module_cache = None
_mail_service_instance = None

module_name = os.getenv("SPLENT_APP", "splent_app")
dotenv_path = PathUtils.get_app_env_file()

if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path, override=True)
else:
    raise FileNotFoundError(f"⚠️ .env file not found at {dotenv_path}")


def install_features_if_needed():
    """Ensure all features in features.txt are installed and their src paths are in sys.path."""
    features_file = f"/workspace/{module_name}/features.txt"
    if not os.path.exists(features_file):
        return

    installed = {dist.metadata["Name"] for dist in distributions()}
    with open(features_file) as f:
        for line in f:
            feature = line.strip()
            if not feature:
                continue

            path = f"/workspace/{feature}"
            pyproject_path = os.path.join(path, "pyproject.toml")
            if not os.path.exists(pyproject_path):
                continue

            with open(pyproject_path, "rb") as f_toml:
                name = tomllib.load(f_toml)["project"]["name"]
                if name not in installed:
                    subprocess.run([sys.executable, "-m", "pip", "install", "-e", path], check=True)

            src_path = os.path.join(path, "src")
            if src_path not in sys.path:
                sys.path.insert(0, src_path)


def get_app_module():
    global _module_cache
    if _module_cache:
        return _module_cache

    install_features_if_needed()

    src_path = f"/workspace/{module_name}/src"
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    try:
        _module_cache = importlib.import_module(module_name)
        return _module_cache
    except ImportError as e:
        raise RuntimeError(f"❌ Failed to import module '{module_name}'\n{e}")


def get_create_app():
    mod = get_app_module()
    if hasattr(mod, "create_app"):
        return mod.create_app
    raise RuntimeError(f"❌ The module '{mod.__name__}' does not define `create_app()`")


def get_app():
    global _app_instance
    if _app_instance is not None:
        return _app_instance
    create_app = get_create_app()
    _app_instance = create_app()
    return _app_instance


def get_db():
    global _db_instance
    if _db_instance is not None:
        return _db_instance

    mod = get_app_module()
    if hasattr(mod, "get_db") and callable(mod.get_db):
        _db_instance = mod.get_db()
    elif hasattr(mod, "db"):
        app = get_app()
        mod.db.init_app(app)
        _db_instance = mod.db
    else:
        raise RuntimeError(
            f"❌ Module '{mod.__name__}' must define either `get_db()` or `db`."
        )
    return _db_instance


def get_mail_service():
    global _mail_service_instance
    if _mail_service_instance is not None:
        return _mail_service_instance

    mod = get_app_module()
    if hasattr(mod, "mail_service"):
        _mail_service_instance = mod.mail_service
        return _mail_service_instance

    raise RuntimeError(
        f"❌ Module '{mod.__name__}' does not expose `mail_service`."
    )
