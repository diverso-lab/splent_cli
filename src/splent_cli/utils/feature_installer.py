import subprocess
from pathlib import Path
import tomllib

from splent_cli.utils.feature_utils import get_features_from_pyproject


def get_installed_packages() -> set[str]:
    result = subprocess.run(
        ["pip", "list", "--format=freeze"],
        stdout=subprocess.PIPE,
        text=True,
        check=True,
    )
    return {
        line.split("==")[0]
        for line in result.stdout.strip().splitlines()
        if "==" in line
    }


def get_package_name(feature_path: Path) -> str | None:
    pyproject_path = feature_path / "pyproject.toml"
    if not pyproject_path.is_file():
        return None
    try:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("name")
    except Exception:
        return None


def ensure_editable_features_installed():
    features = get_features_from_pyproject()
    installed = get_installed_packages()

    for feature in features:
        feature_path = Path("/workspace") / feature
        package_name = get_package_name(feature_path)

        if not package_name:
            print(f"⚠️  Skipping {feature}: no valid pyproject.toml or name.")
            continue

        if package_name in installed:
            continue

        print(f"➡️ Installing {package_name} in editable mode...")
        subprocess.run(["pip", "install", "-e", str(feature_path)], check=True)
