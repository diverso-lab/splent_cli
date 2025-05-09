import os
import tomllib
from splent_cli.utils.path_utils import PathUtils


def get_features_from_pyproject():
    """
    Devuelve la lista de features declaradas en [project.optional-dependencies].features del pyproject.toml
    """
    pyproject_path = os.path.join(PathUtils.get_app_base_dir(), "pyproject.toml")

    if not os.path.exists(pyproject_path):
        return []

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data["project"]["optional-dependencies"].get("features", [])
    except Exception:
        return []


def get_normalize_feature_name_in_splent_format(name):
    return name if name.startswith("splent_feature_") else f"splent_feature_{name}"