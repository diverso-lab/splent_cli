import os
import re
import shutil
import requests
import click
from splent_cli.services import context
from splent_cli.utils.cache_utils import make_feature_readonly
from splent_cli.utils.feature_utils import normalize_namespace
from splent_cli.utils.proc import run, require_tool


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# =====================================================================
# UTILS
# =====================================================================


def _get_latest_tag(namespace, repo) -> str | None:
    """Fetch the highest semver tag from GitHub. Returns None if unreachable or no tags."""
    api_url = f"https://api.github.com/repos/{namespace}/{repo}/tags?per_page=100"
    try:
        r = requests.get(api_url, timeout=5)
        r.raise_for_status()
        tags = r.json()
        if not tags:
            return None
        versions = []
        for tag in tags:
            name = tag.get("name", "")
            m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", name)
            if m:
                versions.append(
                    (int(m.group(1)), int(m.group(2)), int(m.group(3)), name)
                )
        if not versions:
            return tags[0]["name"]
        versions.sort(reverse=True)
        return versions[0][3]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def _build_repo_url(namespace, repo):
    """Build the Git URL, trying SSH first with HTTPS fallback.

    Returns a tuple (real_url, display_url) where display_url never contains a token.
    """
    from splent_cli.utils.git_url import build_git_url

    return build_git_url(namespace, repo)


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
        version = _get_latest_tag(namespace, repo)
        if not version:
            click.secho(
                f"❌ Could not fetch tags for {namespace}/{repo}. Is the repo reachable and does it have tags?",
                fg="red",
            )
            raise SystemExit(1)

    # Build Git URL based on your ownership
    fork_url, display_url = _build_repo_url(namespace, repo)

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

    click.secho(f"⬇️ Cloning {display_url}@{version}", fg="cyan")

    # Try clone the specific tag/branch (suppress git noise). Capture output so
    # we can tell WHY it failed instead of blindly assuming "version not found".
    result = run(
        [
            "git",
            "-c",
            "advice.detachedHead=false",
            "clone",
            "--depth",
            "1",
            "--branch",
            version,
            "--quiet",
            fork_url,
            local_path,
        ],
        check=False,
        capture=True,
    )

    if result.returncode != 0:
        shutil.rmtree(local_path, ignore_errors=True)
        stderr = (result.stderr or result.stdout or "").strip()
        stderr_lc = stderr.lower()

        # A genuinely missing ref is the ONLY case where falling back to main is
        # correct. git reports it as e.g.:
        #   "Remote branch <x> not found in upstream origin"
        #   "Could not find remote branch <x> to clone"
        ref_not_found = (
            "remote branch" in stderr_lc and "not found" in stderr_lc
        ) or "could not find remote branch" in stderr_lc

        # Auth / network / repo-access failures must NOT silently install main.
        auth_or_network = any(
            token in stderr_lc
            for token in (
                "authentication failed",
                "permission denied",
                "could not read from remote repository",
                "repository not found",
                "could not resolve host",
                "connection timed out",
                "connection refused",
                "network is unreachable",
                "failed to connect",
                "ssl",
                "tls",
            )
        )

        if ref_not_found and not auth_or_network:
            click.secho(
                f"⚠️ Version '{version}' not found. Cloning main instead.", fg="yellow"
            )
            fallback = run(
                ["git", "clone", "--depth", "1", "--quiet", fork_url, local_path],
                check=False,
                capture=True,
            )
            if fallback.returncode != 0:
                shutil.rmtree(local_path, ignore_errors=True)
                detail = (fallback.stderr or fallback.stdout or "").strip()
                click.secho(
                    f"❌ Repository '{namespace}/{repo}' not found or not accessible.",
                    fg="red",
                )
                if detail:
                    click.secho(detail, fg="red")
                raise SystemExit(1)
        else:
            # Network / auth / unknown git failure: do NOT fall back to main,
            # which would silently install the wrong (unpinned) code.
            click.secho(
                f"❌ Failed to clone '{namespace}/{repo}@{version}'.", fg="red"
            )
            click.secho(
                "This looks like a network, authentication, or repository-access "
                "problem rather than a missing version. Check your connection and "
                "credentials, then try again.",
                fg="red",
            )
            if stderr:
                click.secho(stderr, fg="red")
            raise SystemExit(1)

    # Lock files as read-only to prevent accidental edits on pinned features
    make_feature_readonly(local_path)

    click.secho(
        f"✅ Feature '{namespace}/{repo}@{version}' cloned successfully.", fg="green"
    )
    click.secho(f"🔒 Cached (read-only) at: {local_path}", fg="blue")
