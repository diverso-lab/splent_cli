"""
release:cli / release:framework — Release core SPLENT packages.

Thin wrappers around the shared release pipeline in services/release.py.
"""

import os
import click
from splent_cli.services import context, release


def _wizard_from_remote(package_path: str) -> str:
    """Run the semver wizard using the org/repo from git remote."""
    repo = release.get_repo_from_path(package_path)
    org, repo_name = repo.split("/")
    return release.semver_wizard(org, repo_name)


@click.command(
    "release:cli",
    short_help="Release splent_cli: bump version, tag, publish to GitHub/PyPI.",
)
@click.argument("version", required=False, default=None)
def release_cli(version: str | None):
    workspace = str(context.workspace())
    package_path = os.path.join(workspace, "splent_cli")

    if not version:
        version = _wizard_from_remote(package_path)

    release.run_release_pipeline("splent_cli", package_path, version)


@click.command(
    "release:framework",
    short_help="Release splent_framework: bump version, tag, publish to GitHub/PyPI.",
)
@click.argument("version", required=False, default=None)
def release_framework(version: str | None):
    workspace = str(context.workspace())
    package_path = os.path.join(workspace, "splent_framework")

    if not version:
        version = _wizard_from_remote(package_path)

    release.run_release_pipeline("splent_framework", package_path, version)
