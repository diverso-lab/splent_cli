import os
import sys
import subprocess
import tomllib
import importlib
from dotenv import load_dotenv
from importlib.metadata import distributions
from flask import Flask

from splent_cli.utils.path_utils import PathUtils

load_dotenv()

_app_instance = None
_module_cache = None

module_name = os.getenv("SPLENT_APP")
dotenv_path = None

if module_name:
    dotenv_path = PathUtils.get_app_env_file()
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path, override=True)


def install_features_if_needed():
    """Ensure all features from pyproject.toml are installed and their src paths are in sys.path."""
    if not module_name:
        return  # No app defined yet

    pyproject_path = f"/workspace/{module_name}/pyproject.toml"
    if not os.path.exists(pyproject_path):
        return

    installed = {dist.metadata["Name"] for dist in distributions()}
    with open(pyproject_path, "rb") as f_toml:
        pyproject = tomllib.load(f_toml)

    features = (
        pyproject.get("project", {})
        .get("optional-dependencies", {})
        .get("features", [])
    )
    for feature in features:
        path = f"/workspace/{feature}"
        pyproject_feature = os.path.join(path, "pyproject.toml")
        if not os.path.exists(pyproject_feature):
            continue

        with open(pyproject_feature, "rb") as f_feat:
            name = tomllib.load(f_feat)["project"]["name"]
            if name not in installed:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", path], check=True
                )

        src_path = os.path.join(path, "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)


def get_app_module():
    global _module_cache
    if not module_name:
        raise RuntimeError(
            "❌ No SPLENT_APP defined — cannot import application module."
        )
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


def get_create_app_in_testing_mode():
    mod = get_app_module()
    if not hasattr(mod, "create_app"):
        raise RuntimeError(f"❌ The module '{mod.__name__}' no define create_app()")
    return mod.create_app("testing")


def get_app():
    global _app_instance
    if _app_instance is not None:
        return _app_instance
    create_app = get_create_app()
    _app_instance = create_app()
    return _app_instance


def get_current_app_config_value(key: str):
    app: Flask = get_create_app_in_testing_mode()
    return app.config.get(key)
