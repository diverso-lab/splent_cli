from pathlib import Path
import tomllib

import click

from splent_cli.utils.feature_utils import get_features_from_pyproject
from splent_cli.utils.path_utils import PathUtils
from splent_cli.utils.proc import run


def _workspace_root() -> Path:
    """Resolve the workspace root the same way template_drift does.

    Uses PathUtils/WORKING_DIR so this works on a host/dev box instead of
    assuming a hardcoded ``/workspace`` that only exists inside containers.
    """
    return Path(PathUtils.get_working_dir() or "/workspace")


def get_installed_packages() -> set[str]:
    result = run(
        ["pip", "list", "--format=freeze"],
        capture=True,
        tool_hint="Install pip / ensure your Python environment is active.",
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
    except (OSError, tomllib.TOMLDecodeError):
        return None


def ensure_editable_features_installed():
    features = get_features_from_pyproject()
    installed = get_installed_packages()
    workspace = _workspace_root()

    failed: list[str] = []

    for feature in features:
        feature_path = workspace / feature
        package_name = get_package_name(feature_path)

        if not package_name:
            click.secho(
                f"⚠️  Skipping {feature}: no valid pyproject.toml or name "
                f"(looked in {feature_path}).",
                fg="yellow",
            )
            continue

        if package_name in installed:
            continue

        click.echo(f"➡️  Installing {package_name} in editable mode...")
        try:
            run(
                ["pip", "install", "-e", str(feature_path)],
                capture=True,
                tool_hint="Install pip / ensure your Python environment is active.",
            )
        except click.ClickException as exc:
            failed.append(package_name)
            click.secho(
                f"⚠️  Failed to install {package_name}: {exc.format_message()}",
                fg="red",
            )
            continue

    if failed:
        click.secho(
            f"⚠️  {len(failed)} feature(s) failed to install: {', '.join(failed)}.",
            fg="red",
        )
