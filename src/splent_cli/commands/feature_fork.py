import os
import click
import requests
import subprocess


@click.command("feature:fork", help="Create a GitHub fork of a SPLENT feature and add it to the local cache")
@click.argument("feature_name", required=True)
@click.option("--name", "-n", required=True, help="Name for the new fork (e.g. splent_feature_auth_experimental)")
@click.option("--org", help="GitHub org/user to fork into (default: current user)")
def fork(feature_name, name, org):
    """
    Forks a SPLENT feature repository from GitHub, clones it locally, and registers it in .splent_cache/features.
    Requires GITHUB_TOKEN in the environment.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        click.secho("✖ Error: GITHUB_TOKEN not set in environment", fg="red")
        raise SystemExit(1)

    workspace = os.getenv("WORKING_DIR", "/workspace")
    cache_root = os.path.join(workspace, ".splent_cache", "features")
    os.makedirs(cache_root, exist_ok=True)

    # Assume official upstream org
    upstream_owner = "splent-io"
    upstream_repo = feature_name

    api_url = f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}/forks"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    payload = {"default_branch_only": False}
    if org:
        payload["organization"] = org

    click.secho(f"→ Forking {upstream_owner}/{upstream_repo} on GitHub...", fg="cyan")

    response = requests.post(api_url, headers=headers, json=payload)
    if response.status_code not in (201, 202):
        click.secho(f"✖ Failed to create fork: {response.status_code} {response.text}", fg="red")
        raise SystemExit(2)

    fork_data = response.json()
    fork_full_name = fork_data["full_name"]
    fork_clone_url = fork_data["ssh_url"]

    local_path = os.path.join(cache_root, name)

    if os.path.exists(local_path):
        click.secho(f"✖ Local folder already exists: {local_path}", fg="red")
        raise SystemExit(2)

    click.secho(f"✔ Created fork {fork_full_name}", fg="green")
    click.secho(f"→ Cloning into {local_path} ...", fg="cyan")

    try:
        subprocess.run(["git", "clone", fork_clone_url, local_path], check=True)
    except subprocess.CalledProcessError as e:
        click.secho(f"✖ Git clone failed: {e}", fg="red")
        raise SystemExit(2)

    click.secho(f"✔ Fork cloned successfully to {local_path}", fg="green")

    _register_in_cache(cache_root, name, fork_clone_url)
    click.secho(f"✔ Fork '{name}' registered in cache ✅", fg="green")


# -----------------------------
# Helpers
# -----------------------------

def _register_in_cache(cache_root: str, feature_name: str, repo_url: str):
    """Add or update entry in .splent_cache/features/index.txt"""
    index_path = os.path.join(cache_root, "index.txt")
    entry = f"{feature_name} {repo_url}\n"

    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        lines = [l for l in lines if not l.startswith(feature_name + " ")]
    else:
        lines = []

    lines.append(entry)
    with open(index_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
