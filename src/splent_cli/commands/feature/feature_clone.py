import os
import re
import shutil
import click
from splent_cli.services import context, registry
from splent_cli.utils.cache_utils import make_feature_readonly
from splent_cli.utils.feature_utils import normalize_namespace
from splent_cli.utils.proc import require_tool


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# =====================================================================
# UTILS
# =====================================================================


def _parse_full_name(full_name: str):
    """
    full_name = <namespace>/<repo>[@version]
    """
    if "/" not in full_name:
        raise SystemExit("❌ Invalid format. Use <namespace>/<repo>[@version]")

    namespace, rest = full_name.split("/", 1)

    if "@" in rest:
        repo, version = rest.split("@", 1)
    else:
        repo = rest
        version = None

    return namespace, repo, version


def _validate_identifier_part(value: str, label: str):
    if not re.fullmatch(r"[a-zA-Z0-9_\-\.]+", value):
        raise SystemExit(
            f"❌ Invalid {label}: '{value}'. Only letters, digits, - _ . allowed."
        )


# =====================================================================
# MAIN
# =====================================================================


@click.command(
    "feature:clone",
    short_help="Clone a feature into the local cache.",
    help="Clone a feature into the local cache namespace.",
)
@click.argument("full_name", required=True)
def feature_clone(full_name):
    """
    Clone <namespace>/<repo> into:
      .splent_cache/features/<namespace>/<repo>@<version>

    - If version is omitted, fetches the latest tag or 'main'.
    """

    namespace, repo, version = _parse_full_name(full_name)

    _validate_identifier_part(namespace, "namespace")
    _validate_identifier_part(repo, "repo")
    if version:
        _validate_identifier_part(version, "version")

    if not version:
        click.echo(
            f"🔍 No version provided → fetching latest tag for {namespace}/{repo}..."
        )
        version = registry.latest_semver_tag(namespace, repo)
        if not version:
            click.secho(
                f"❌ Could not fetch tags for {namespace}/{repo}. Is the repo reachable and does it have tags?",
                fg="red",
            )
            raise SystemExit(1)

    # Local destination
    namespace_safe = normalize_namespace(namespace)
    workspace = str(context.workspace())
    cache_dir = os.path.join(workspace, ".splent_cache", "features", namespace_safe)
    os.makedirs(cache_dir, exist_ok=True)

    local_path = os.path.join(cache_dir, f"{repo}@{version}")

    if os.path.exists(local_path):
        click.secho(f"⚠️ Folder already exists: {local_path}", fg="yellow")
        return

    # Fail early with an actionable message if git is missing, instead of
    # surfacing a raw FileNotFoundError traceback.
    require_tool(
        "git",
        "Install Git from https://git-scm.com/downloads and make sure it is on your PATH.",
    )

    from splent_cli.utils.git_url import (
        clone as git_clone,
        CLONE_SUCCESS,
        CLONE_REF_NOT_FOUND,
    )

    click.secho(f"⬇️ Cloning {namespace}/{repo}@{version}", fg="cyan")

    # Always try SSH first, then HTTPS (GITHUB_TOKEN if set, else anonymous). The
    # transport is decided per repo from the real clone result (see
    # git_url.clone), so a working SSH key that simply can't read THIS repo no
    # longer blocks the HTTPS path.
    outcome, used_url, stderr = git_clone(namespace, repo, local_path, ref=version)

    # A genuinely missing tag/branch is the ONLY case where falling back to the
    # default branch is correct (the repo itself was reachable).
    if outcome == CLONE_REF_NOT_FOUND:
        click.secho(
            f"⚠️ Version '{version}' not found. Cloning default branch instead.",
            fg="yellow",
        )
        outcome, used_url, stderr = git_clone(namespace, repo, local_path, ref=None)

    if outcome != CLONE_SUCCESS:
        shutil.rmtree(local_path, ignore_errors=True)
        click.secho(
            f"❌ Repository '{namespace}/{repo}' not found or not accessible "
            "(tried SSH and HTTPS).",
            fg="red",
        )
        click.secho(
            "Check your network, that the repo exists, and that you have read "
            "access — an SSH key authorised for the repo, or a GITHUB_TOKEN with "
            "read scope in your .env.",
            fg="red",
        )
        if stderr:
            click.secho(stderr, fg="red")
        raise SystemExit(1)

    # Lock files as read-only to prevent accidental edits on pinned features
    make_feature_readonly(local_path)

    click.secho(
        f"✅ Feature '{namespace}/{repo}@{version}' cloned successfully ({used_url}).",
        fg="green",
    )
    click.secho(f"🔒 Cached (read-only) at: {local_path}", fg="blue")
