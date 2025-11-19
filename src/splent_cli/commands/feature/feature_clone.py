import os
import subprocess
import requests
import click


DEFAULT_NAMESPACE = os.getenv('SPLENT_DEFAULT_NAMESPACE', 'splent_io')
WORKSPACE = os.getenv("WORKING_DIR", "/workspace")


# =====================================================================
# UTILS
# =====================================================================

def _get_latest_tag(namespace, repo):
    """Fetch the latest tag from GitHub (or 'main' if none)."""
    api_url = f"https://api.github.com/repos/{namespace}/{repo}/tags"
    try:
        r = requests.get(api_url, timeout=5)
        r.raise_for_status()
        tags = r.json()
        if tags:
            return tags[0]["name"]
        return "main"
    except Exception:
        return "main"


def _build_repo_url(namespace, repo):
    """
    Build the Git URL depending on SPLENT_USE_SSH.
    Priority:
      1. If SPLENT_USE_SSH=true → SSH
      2. Else if GITHUB_TOKEN exists → HTTPS with token
      3. Else → HTTPS read-only
    """
    use_ssh = os.getenv("SPLENT_USE_SSH", "false").lower() == "true"
    token = os.getenv("GITHUB_TOKEN")

    if use_ssh:
        click.secho("🔐 SSH mode enabled (SPLENT_USE_SSH=true)", fg="cyan")
        return f"git@github.com:{namespace}/{repo}.git"

    if token:
        click.secho("🌐 HTTPS with token (SPLENT_USE_SSH not true)", fg="cyan")
        return f"https://{token}@github.com/{namespace}/{repo}.git"

    click.secho("🌍 HTTPS read-only (no token, no SSH)", fg="yellow")
    return f"https://github.com/{namespace}/{repo}.git"


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


# =====================================================================
# MAIN
# =====================================================================

@click.command(
    "feature:clone",
    short_help="Clone a feature into the local cache.",
    help="Clone a feature into the local cache namespace."
)
@click.argument("full_name", required=True)
def feature_clone(full_name):
    """
    Clone <namespace>/<repo> into:
      .splent_cache/features/<namespace>/<repo>@<version>

    - If version is omitted, fetches the latest tag or 'main'.
    """

    namespace, repo, version = _parse_full_name(full_name)

    if not version:
        click.echo(f"🔍 No version provided → fetching latest tag for {namespace}/{repo}...")
        version = _get_latest_tag(namespace, repo)

    # Build Git URL based on your ownership
    fork_url = _build_repo_url(namespace, repo)

    # Local destination
    namespace_safe = namespace.replace("-", "_").replace(".", "_")
    cache_dir = os.path.join(WORKSPACE, ".splent_cache", "features", namespace_safe)
    os.makedirs(cache_dir, exist_ok=True)

    local_path = os.path.join(cache_dir, f"{repo}@{version}")

    if os.path.exists(local_path):
        click.secho(f"⚠️ Folder already exists: {local_path}", fg="yellow")
        return

    click.secho(f"⬇️ Cloning {fork_url}@{version}", fg="cyan")

    # Try clone specific tag/branch
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", version, fork_url, local_path],
            check=True,
        )
    except subprocess.CalledProcessError:
        click.secho(f"⚠️ Version '{version}' not found. Cloning main instead.", fg="yellow")
        subprocess.run(["git", "clone", "--depth", "1", fork_url, local_path], check=True)

    click.secho(f"✅ Feature '{namespace}/{repo}@{version}' cloned successfully.", fg="green")
    click.secho(f"📦 Cached at: {local_path}", fg="blue")
