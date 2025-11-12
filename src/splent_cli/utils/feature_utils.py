import os
import tomllib
from typing import List
from splent_cli.utils.path_utils import PathUtils


def get_features_from_pyproject() -> List[str]:
    """
    Devuelve la lista de features declaradas en [project.optional-dependencies].features
    del pyproject.toml del producto activo (según SPLENT_APP).

    Returns:
        List[str]: Lista de nombres de features en formato SPLENT, por ejemplo:
            ['splent_feature_redis', 'splent_feature_auth', 'splent_feature_profile']
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


def get_normalize_feature_name_in_splent_format(name: str) -> str:
    """
    Añade el prefijo 'splent_feature_' si no lo tiene ya.

    Args:
        name (str): Nombre simple de la feature, como 'auth' o 'profile'.

    Returns:
        str: Nombre normalizado con prefijo SPLENT, como 'splent_feature_auth'.
    """
    return name if name.startswith("splent_feature_") else f"splent_feature_{name}"
