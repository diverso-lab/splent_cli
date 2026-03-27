import os
import re
import subprocess
import requests
import click
from splent_cli.services import context
from splent_cli.utils.cache_utils import make_feature_readonly


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# =====================================================================
# UTILS
# =====================================================================


def _get_latest_tag(namespace, repo) -> str | None:
    """Fetch the latest tag from GitHub. Returns None if unreachable or no tags exist."""
    api_url = f"https://api.github.com/repos/{namespace}/{repo}/tags"
    try:
        r = requests.get(api_url, timeout=5)
        r.raise_for_status()
        tags = r.json()
        return tags[0]["name"] if tags else None
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def _build_repo_url(namespace, repo):
    """
    Build the Git URL depending on SPLENT_USE_SSH.
    Priority:
      1. If SPLENT_USE_SSH=true → SSH
      2. Else if GITHUB_TOKEN exists → HTTPS with token
      3. Else → HTTPS read-only

    Returns a tuple (real_url, display_url) where display_url never contains a token.
    """
    use_ssh = os.getenv("SPLENT_USE_SSH", "false").lower() == "true"
    token = os.getenv("GITHUB_TOKEN")

    if use_ssh:
        click.secho("🔐 SSH mode enabled (SPLENT_USE_SSH=true)", fg="cyan")
        url = f"git@github.com:{namespace}/{repo}.git"
        return url, url

    if token:
        click.secho("🌐 HTTPS with token (SPLENT_USE_SSH not true)", fg="cyan")
        real_url = f"https://{token}@github.com/{namespace}/{repo}.git"
        display_url = f"https://github.com/{namespace}/{repo}.git"
        return real_url, display_url

    click.secho("🌍 HTTPS read-only (no token, no SSH)", fg="yellow")
    url = f"https://github.com/{namespace}/{repo}.git"
    return url, url


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
    if not re.fullmatch(r'[a-zA-Z0-9_\-\.]+', value):
        raise SystemExit(f"❌ Invalid {label}: '{value}'. Only letters, digits, - _ . allowed.")


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
    namespace_safe = namespace.replace("-", "_").replace(".", "_")
    workspace = str(context.workspace())
    cache_dir = os.path.join(workspace, ".splent_cache", "features", namespace_safe)
    os.makedirs(cache_dir, exist_ok=True)

    local_path = os.path.join(cache_dir, f"{repo}@{version}")

    if os.path.exists(local_path):
        click.secho(f"⚠️ Folder already exists: {local_path}", fg="yellow")
        return

    click.secho(f"⬇️ Cloning {display_url}@{version}", fg="cyan")

    # Try clone specific tag/branch (suppress git noise)
    try:
        subprocess.run(
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
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        import shutil
        shutil.rmtree(local_path, ignore_errors=True)
        click.secho(
            f"⚠️ Version '{version}' not found. Cloning main instead.", fg="yellow"
        )
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--quiet", fork_url, local_path],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            shutil.rmtree(local_path, ignore_errors=True)
            click.secho(
                f"❌ Repository '{namespace}/{repo}' not found or not accessible.",
                fg="red",
            )
            raise SystemExit(1)

    # Lock files as read-only to prevent accidental edits on pinned features
    make_feature_readonly(local_path)

    click.secho(
        f"✅ Feature '{namespace}/{repo}@{version}' cloned successfully.", fg="green"
    )
    click.secho(f"🔒 Cached (read-only) at: {local_path}", fg="blue")
