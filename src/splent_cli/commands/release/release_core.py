"""
release:cli / release:framework — Release core SPLENT packages.

Thin wrappers around the shared release pipeline in services/release.py.
"""

import os
import click
from splent_cli.services import context, release


def _wizard_from_remote(package_path: str, channels, resume_target: str) -> str:
    """Run the semver wizard using the org/repo from git remote."""
    repo = release.get_repo_from_path(package_path)
    org, repo_name = repo.split("/")
    package = (
        release.read_project_name(os.path.join(package_path, "pyproject.toml"))
        or repo_name
    )
    return release.semver_wizard(
        org,
        repo_name,
        package=package,
        channels=channels,
        resume_target=resume_target,
        path=package_path,
    )


def _channel_options(fn):
    """The two escape-hatch flags, shared by both core release commands."""
    fn = click.option(
        "--no-pypi",
        is_flag=True,
        help="Publish to GitHub only. The package will NOT exist on PyPI for this version.",
    )(fn)
    fn = click.option(
        "--no-github",
        is_flag=True,
        help="Publish to PyPI only. Nothing at all is pushed to GitHub for this version.",
    )(fn)
    fn = click.option(
        "--allow-unfinished",
        is_flag=True,
        help="Release even though the previous version never finished publishing.",
    )(fn)
    return fn


@click.command(
    "release:cli",
    short_help="Release splent_cli: bump version, tag, publish to GitHub/PyPI.",
)
@click.argument("version", required=False, default=None)
@click.option(
    "--skip-checks",
    is_flag=True,
    help="Skip the pre-release lint + tests gate (emergencies only).",
)
@_channel_options
def release_cli(
    version: str | None,
    skip_checks: bool,
    no_pypi: bool,
    no_github: bool,
    allow_unfinished: bool,
):
    workspace = str(context.workspace())
    package_path = os.path.join(workspace, "splent_cli")

    channels = release.resolve_channels(
        package_path, no_github=no_github, no_pypi=no_pypi
    )
    release.confirm_single_channel(channels, no_github=no_github, no_pypi=no_pypi)

    if not version:
        version = _wizard_from_remote(package_path, channels, "cli")

    release.run_release_pipeline(
        "splent_cli",
        package_path,
        version,
        kind="cli",
        skip_checks=skip_checks,
        channels=channels,
        resume_target="cli",
        allow_unfinished=allow_unfinished,
    )


@click.command(
    "release:framework",
    short_help="Release splent_framework: bump version, tag, publish to GitHub/PyPI.",
)
@click.argument("version", required=False, default=None)
@click.option(
    "--skip-checks",
    is_flag=True,
    help="Skip the pre-release lint + tests gate (emergencies only).",
)
@_channel_options
def release_framework(
    version: str | None,
    skip_checks: bool,
    no_pypi: bool,
    no_github: bool,
    allow_unfinished: bool,
):
    workspace = str(context.workspace())
    package_path = os.path.join(workspace, "splent_framework")

    channels = release.resolve_channels(
        package_path, no_github=no_github, no_pypi=no_pypi
    )
    release.confirm_single_channel(channels, no_github=no_github, no_pypi=no_pypi)

    if not version:
        version = _wizard_from_remote(package_path, channels, "framework")

    release.run_release_pipeline(
        "splent_framework",
        package_path,
        version,
        kind="framework",
        skip_checks=skip_checks,
        channels=channels,
        resume_target="framework",
        allow_unfinished=allow_unfinished,
    )
