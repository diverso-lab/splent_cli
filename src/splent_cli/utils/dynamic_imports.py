import os
import importlib
import sys
from dotenv import load_dotenv

_app_instance = None  # Singleton

# 1. Intenta obtener el nombre del módulo antes de cargar su .env
module_name = os.environ.get("SPLENT_APP_MODULE", "splent_app")

# 2. Construye la ruta del .env correspondiente
dotenv_path = f"/workspace/splent_docker/products/{module_name}/.env"

# 3. Carga el .env (sobrescribiendo si ya había variables)
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path, override=True)
else:
    raise FileNotFoundError(f"⚠️ No se encontró el archivo .env en {dotenv_path}")


def get_app_module():
    # Ahora sí: volvemos a obtener el valor, por si fue sobreescrito en el .env
    module_name = os.getenv("SPLENT_APP_MODULE", "splent_app")

    # Añade el path de src
    sys.path.insert(0, f"/workspace/{module_name}/src")

    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        raise RuntimeError(f"❌ No se pudo importar el módulo '{module_name}'\n{e}")


def get_create_app():
    mod = get_app_module()
    if hasattr(mod, "create_app"):
        return mod.create_app
    raise RuntimeError(f"❌ El módulo '{mod.__name__}' no tiene 'create_app()'")


def get_app():
    global _app_instance
    if _app_instance is None:
        create_app = get_create_app()
        _app_instance = create_app()
    return _app_instance


def get_db():
    mod = get_app_module()
    if hasattr(mod, "get_db") and callable(mod.get_db):
        return mod.get_db()
    if hasattr(mod, "db"):
        app = get_app()
        mod.db.init_app(app)
        return mod.db
    raise RuntimeError(
        f"❌ El módulo '{mod.__name__}' no tiene ni 'get_db()' ni 'db'."
    )