import os
import subprocess
from pathlib import Path
import click
from splent_cli.utils.feature_utils import get_normalize_feature_name_in_splent_format


def _find_feature_root(pkg: str, workspace: Path, product: str) -> Path | None:
    # 1. Product symlinks
    features_base = workspace / product / "features"
    if features_base.is_dir():
        for ns_dir in features_base.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                if entry.name.split("@")[0] == pkg:
                    return entry.resolve()

    # 2. Cache directly
    cache_root = workspace / ".splent_cache" / "features"
    if cache_root.is_dir():
        for ns_dir in cache_root.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                if entry.is_dir() and entry.name.split("@")[0] == pkg:
                    return entry

    # 3. Bare workspace clone
    bare = workspace / pkg
    if bare.is_dir():
        return bare

    return None


@click.command(
    "feature:git",
    short_help="Run a git command inside a feature's directory.",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("feature_ref")
@click.argument("git_args", nargs=-1, type=click.UNPROCESSED)
def feature_git(feature_ref, git_args):
    """
    Run any git command inside the resolved feature directory.

    \b
    FEATURE_REF  Feature name (e.g. auth, splent_feature_auth)
    GIT_ARGS     Any git subcommand and its arguments

    Examples:
        splent feature:git auth status
        splent feature:git auth commit -m "fix test patch path"
        splent feature:git auth push
        splent feature:git auth log --oneline -5
        splent feature:git auth diff HEAD~1
    """
    workspace = Path(os.getenv("WORKING_DIR", "/workspace"))
    product = os.getenv("SPLENT_APP")

    if not product:
        click.secho("❌ SPLENT_APP is not set.", fg="red")
        raise SystemExit(1)

    if not git_args:
        click.secho("❌ No git command provided.", fg="red")
        raise SystemExit(1)

    pkg = get_normalize_feature_name_in_splent_format(feature_ref)
    root = _find_feature_root(pkg, workspace, product)

    if not root:
        click.secho(f"❌ Feature '{pkg}' not found in cache or workspace.", fg="red")
        raise SystemExit(1)

    click.secho(f"  📁 {root}", fg="bright_black")
    result = subprocess.run(["git"] + list(git_args), cwd=root)
    raise SystemExit(result.returncode)


cli_command = feature_git
