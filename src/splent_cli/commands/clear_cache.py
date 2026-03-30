import click
import shutil
import os
from pathlib import Path

from splent_cli.utils.path_utils import PathUtils
from splent_cli.services import context


def clean_build_artifacts(target_path: str | Path, *, quiet: bool = False):
    """Remove __pycache__, .pyc, .pytest_cache, build/, dist/, and *.egg-info
    under *target_path*.

    When *quiet* is True only errors are printed (used by the release pipeline).
    """
    root = Path(target_path)

    # dist/ and build/
    for name in ("dist", "build"):
        d = root / name
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    # *.egg-info
    for d in root.glob("**/*.egg-info"):
        shutil.rmtree(d, ignore_errors=True)

    # .pytest_cache
    for d in root.rglob(".pytest_cache"):
        shutil.rmtree(d, ignore_errors=True)

    # __pycache__
    removed = 0
    for d in root.rglob("__pycache__"):
        try:
            shutil.rmtree(d)
            removed += 1
        except Exception as e:
            if not quiet:
                click.secho(f"  warn: could not remove {d}: {e}", fg="yellow")

    # stray .pyc files
    for f in root.rglob("*.pyc"):
        try:
            f.unlink()
        except Exception:
            pass

    if not quiet:
        click.secho(f"  Cleared build artifacts ({removed} __pycache__ dirs removed).", fg="green")


@click.command(
    "clear:pycache",
    short_help="Clear __pycache__, .pytest_cache and build artifacts from the workspace.",
)
def clear_cache():
    if not click.confirm(
        "Are you sure you want to clear caches and build artifacts?"
    ):
        click.secho("  Cancelled.", fg="yellow")
        return

    clean_build_artifacts(context.workspace())
