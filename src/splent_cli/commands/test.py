import os
import sys
import subprocess
from pathlib import Path
import click
from splent_cli.utils.dynamic_imports import get_current_app_config_value
from splent_cli.utils.feature_utils import (
    get_features_from_pyproject,
    get_normalize_feature_name_in_splent_format,
)


@click.command("test", help="Runs pytest on registered SPLENT features.")
@click.argument("feature_name", required=False)
@click.option("-k", "keyword", help="Only run tests that match the given keyword.")
def test(feature_name, keyword):
    validate_testing_environment()

    feature_dirs = get_feature_dirs(feature_name)
    test_paths = collect_test_paths(feature_dirs)

    if not test_paths:
        click.echo(click.style("⚠️ No test directories found.", fg="yellow"))
        return

    click.echo(click.style(f"📂 Found {len(test_paths)} test directories", fg="cyan"))
    run_pytest_individually(test_paths, keyword)


# ─────────────────────────────────────────────────────────────────────────────

def validate_testing_environment():
    env = get_current_app_config_value("TESTING")
    db_url = get_current_app_config_value("SQLALCHEMY_DATABASE_URI")

    if not env:
        raise AssertionError(f"❌ TESTING mode is not active. Current DB: {db_url}")
    if "test" not in db_url.lower():
        raise AssertionError(f"❌ Non-test database in use: {db_url}")


def get_feature_dirs(feature_name=None) -> list[Path]:
    workspace = Path(os.getenv("WORKSPACE", "/workspace"))
    splent_app = os.getenv("SPLENT_APP")

    if not splent_app:
        click.echo(click.style("❌ SPLENT_APP is not set.", fg="red"))
        raise click.Abort()

    features = get_features_from_pyproject()
    if not features:
        click.echo(click.style(f"⚠️ No features registered in {splent_app}/pyproject.toml", fg="yellow"))
        raise click.Abort()

    if feature_name:
        full_name = get_normalize_feature_name_in_splent_format(feature_name)
        if full_name not in features:
            click.echo(click.style(f"❌ Feature '{full_name}' is not registered.", fg="red"))
            raise click.Abort()

        feature_path = workspace / full_name
        if not feature_path.is_dir():
            click.echo(click.style(f"❌ Feature directory not found: {feature_path}", fg="red"))
            raise click.Abort()

        click.echo(f"🧪 Running tests for feature '{full_name}'...")
        return [feature_path]

    click.echo(f"🧪 Running tests for all features in '{splent_app}'...")
    return [workspace / f for f in features if (workspace / f).is_dir()]


def collect_test_paths(feature_dirs: list[Path]) -> list[str]:
    test_paths = []

    for feature_dir in feature_dirs:
        pkg_name = feature_dir.name
        src_path = feature_dir / "src"
        test_dir = src_path / pkg_name / "tests"

        if test_dir.is_dir():
            test_paths.append(str(test_dir))

    return test_paths


def run_pytest_individually(test_paths: list[str], keyword: str | None):
    for test_path in test_paths:
        test_dir = Path(test_path)  # .../src/splent_feature_xxx/tests
        src_dir = test_dir.parent.parent      # .../src
        feature_dir = src_dir.parent          # .../splent_feature_xxx
        feature_name = feature_dir.name.replace("splent_feature_", "")  # Ej: auth, profile, reset
        relative_path = test_dir.relative_to(src_dir)  # tests/...

        env = os.environ.copy()
        env["PYTHONPATH"] = str(src_dir)

        cmd = [
            "pytest",
            "-v",
            "--rootdir=.",
            "--ignore-glob=*selenium*",
            "-W", "ignore::DeprecationWarning"
        ]

        if keyword:
            cmd.extend(["-k", keyword])

        try:
            subprocess.run(cmd, check=True, env=env, cwd=src_dir)
        except subprocess.CalledProcessError as e:
            click.echo(click.style(f"❌ Tests failed in {feature_name}: {e}", fg="red"))