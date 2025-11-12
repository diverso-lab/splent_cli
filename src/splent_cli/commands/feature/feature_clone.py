import os
import subprocess
import requests
import click


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


def _repo_is_private(namespace, repo):
    """Check if a GitHub repo is private using the API."""
    api_url = f"https://api.github.com/repos/{namespace}/{repo}"
    headers = {}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    try:
        r = requests.get(api_url, headers=headers, timeout=5)
        if r.status_code == 404:
            return True  # cannot access → probably private
        data = r.json()
        return data.get("private", False)
    except Exception:
        return False


@click.command("feature:clone", help="Clone a SPLENT feature into the local cache namespace.")
@click.argument("full_name", required=True)
def feature_clone(full_name):
    """
    Clone <namespace>/<repo> into .splent_cache/features/<namespace>/<repo>@<version>.
    - If no version is specified, uses the latest Git tag or 'main'.
    - Detects whether repo is public or private to choose HTTPS or SSH.
    """

    workspace = os.getenv("WORKING_DIR", "/workspace")

    if "@" in full_name:
        namespace, rest = full_name.split("/", 1)
        repo, version = rest.split("@", 1)
    else:
        if "/" not in full_name:
            click.secho("❌ Invalid format. Use <namespace>/<repo>", fg="red")
            raise SystemExit(1)
        namespace, repo = full_name.split("/", 1)
        version = _get_latest_tag(namespace, repo)

    click.echo(f"🌐 Checking repository: {namespace}/{repo}")
    is_private = _repo_is_private(namespace, repo)

    # --- Clone URL ---
    if is_private:
        fork_url = f"git@github.com:{namespace}/{repo}.git"
        click.secho("🔒 Private repository detected, using SSH.", fg="cyan")
    else:
        fork_url = f"https://github.com/{namespace}/{repo}.git"
        click.secho("🌍 Public repository detected, using HTTPS.", fg="cyan")

    # --- Local path ---
    namespace_safe = namespace.replace("-", "_").replace(".", "_")
    cache_dir = os.path.join(workspace, ".splent_cache", "features", namespace_safe)
    os.makedirs(cache_dir, exist_ok=True)
    local_path = os.path.join(cache_dir, f"{repo}@{version}")

    if os.path.exists(local_path):
        click.secho(f"⚠️ Folder already exists: {local_path}", fg="yellow")
        return

    click.secho(f"⬇️ Cloning {fork_url}@{version}", fg="cyan")

    # --- Clone ---
    try:
        subprocess.run(["git", "clone", "--depth", "1", "--branch", version, fork_url, local_path], check=True)
    except subprocess.CalledProcessError:
        click.secho(f"⚠️ Tag {version} not found. Cloning main branch instead.", fg="yellow")
        subprocess.run(["git", "clone", "--depth", "1", fork_url, local_path], check=True)

    click.secho(f"✅ Feature '{namespace}/{repo}@{version}' cloned successfully.", fg="green")
    click.secho(f"📦 Cached at: {local_path}", fg="blue")
