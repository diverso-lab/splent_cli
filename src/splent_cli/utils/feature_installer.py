import os
import sys
import subprocess
import tomllib  # Python 3.11+
from importlib.metadata import distributions


def ensure_editable_features_installed():
    """Installs missing editable features defined in features.txt of the SPLENT app."""
    splent_app_name = os.getenv("SPLENT_APP")
    if not splent_app_name:
        print("❌ Environment variable SPLENT_APP not set.")
        return

    features_file = f"/workspace/{splent_app_name}/features.txt"
    if not os.path.isfile(features_file):
        print(f"⚠️  No features.txt found in {splent_app_name}, skipping feature installation.")
        return

    def get_installed_package_names():
        return {dist.metadata["Name"] for dist in distributions() if "Name" in dist.metadata}

    def extract_package_name(pyproject_path):
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
            return pyproject["project"]["name"]

    installed_names = get_installed_package_names()

    with open(features_file) as f:
        for line in f:
            feature = line.strip()
            if not feature:
                continue

            path = os.path.join("/workspace", feature)
            pyproject_path = os.path.join(path, "pyproject.toml")

            if not os.path.isfile(pyproject_path):
                print(f"❌ pyproject.toml not found in {path}. Skipping.")
                continue

            try:
                package_name = extract_package_name(pyproject_path)
            except Exception as e:
                print(f"❌ Could not read package name from {pyproject_path}: {e}")
                continue

            if package_name not in installed_names:
                print(f"➡️ Installing {package_name}...")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", path],
                    check=True
                )
