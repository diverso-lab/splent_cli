import os
import time
import click
import requests
import subprocess
import tomllib


@click.command("feature:fork", help="Create a GitHub fork of a SPLENT feature and add it to the local cache")
@click.argument("feature_name", required=True)
@click.option("--name", "-n", required=True, help="Name for the new fork (both locally and on GitHub)")
def fork(feature_name, name):
    """CLI entrypoint for forking a SPLENT feature"""
    token, github_user = _require_github_credentials()
    workspace, cache_root = _prepare_workspace()

    user_type = _get_github_user_type(github_user, token)
    upstream_owner = _detect_upstream_owner(feature_name, workspace)
    fork_clone_url, fork_html_url = _create_fork(upstream_owner, feature_name, github_user, user_type, token)

    fork_clone_url, fork_html_url = _rename_fork_with_retries(
        feature_name, name, github_user, token, fork_clone_url, fork_html_url
    )

    local_path = _clone_and_configure(
        feature_name, name, upstream_owner, fork_clone_url, cache_root
    )

    _register_in_cache(cache_root, name, fork_clone_url)
    click.secho(f"🌐 GitHub URL: {fork_html_url}", fg="blue")
    click.secho(f"✔ Fork '{name}' registered in cache ✅", fg="green")


# ---------------------------------------------------------------------
# CORE FUNCTIONS
# ---------------------------------------------------------------------

def _require_github_credentials():
    token = os.getenv("GITHUB_TOKEN")
    github_user = os.getenv("GITHUB_USER")
    if not token or not github_user:
        click.secho("✖ Error: GITHUB_TOKEN and GITHUB_USER must be set in the environment", fg="red")
        raise SystemExit(1)
    return token, github_user


def _prepare_workspace():
    workspace = os.getenv("WORKING_DIR", "/workspace")
    cache_root = os.path.join(workspace, ".splent_cache", "features")
    os.makedirs(cache_root, exist_ok=True)
    return workspace, cache_root


def _get_github_user_type(username: str, token: str) -> str:
    """Detect if GITHUB_USER is 'User' or 'Organization'"""
    url = f"https://api.github.com/users/{username}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            return resp.json().get("type", "User")
        click.secho(f"⚠ GitHub API returned {resp.status_code}, assuming 'User'", fg="yellow")
    except Exception as e:
        click.secho(f"⚠ Could not determine GitHub user type: {e}, assuming 'User'", fg="yellow")
    return "User"


def _detect_upstream_owner(feature_name: str, workspace: str) -> str:
    """
    Detect the upstream owner of a feature.
    - If SPLENT_APP is defined, read pyproject.toml from /workspace/{SPLENT_APP}/.
    - If not, assume the command is running in cache mode and return 'splent-io'.
    """
    app_name = os.getenv("SPLENT_APP")

    # Caso 1: sin SPLENT_APP → modo caché
    if not app_name:
        click.secho("→ No SPLENT_APP defined (cache mode). Using 'splent-io' as upstream.", fg="cyan")
        return "splent-io"

    # Caso 2: con SPLENT_APP → leemos el pyproject del producto activo
    pyproject_path = os.path.join(workspace, app_name, "pyproject.toml")
    upstream_owner = "splent-io"

    if not os.path.exists(pyproject_path):
        click.secho(f"⚠ pyproject.toml not found at {pyproject_path}, using 'splent-io' as upstream.", fg="yellow")
        return upstream_owner

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        features = (
            data.get("project", {})
                .get("optional-dependencies", {})
                .get("features", [])
        )

        for entry in features:
            # Puede ser "usuario/splent_feature_auth@v1.0.0" o "splent_feature_auth@v1.0.0"
            if "/" in entry:
                candidate_user, rest = entry.split("/", 1)
                feature_base = rest.split("@", 1)[0]
            else:
                candidate_user = "splent-io"
                feature_base = entry.split("@", 1)[0]

            if feature_base == feature_name:
                upstream_owner = candidate_user
                break

        click.secho(f"→ Detected upstream owner: {upstream_owner}", fg="cyan")
    except Exception as e:
        click.secho(f"⚠ Could not read pyproject.toml ({e}), using 'splent-io'", fg="yellow")

    return upstream_owner


def _create_fork(upstream_owner, feature_name, github_user, user_type, token):
    """Create a fork on GitHub and return its clone + HTML URLs"""
    api_url = f"https://api.github.com/repos/{upstream_owner}/{feature_name}/forks"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    payload = {"default_branch_only": False}
    if user_type == "Organization":
        payload["organization"] = github_user

    click.secho(f"→ Forking {upstream_owner}/{feature_name} into {github_user} ({user_type})...", fg="cyan")
    resp = requests.post(api_url, headers=headers, json=payload)

    if resp.status_code not in (201, 202):
        click.secho(f"✖ Failed to create fork: {resp.status_code} {resp.text}", fg="red")
        raise SystemExit(2)

    data = resp.json()
    return data["ssh_url"], data["html_url"]


def _rename_fork_with_retries(feature_name, name, github_user, token, fork_clone_url, fork_html_url):
    """Rename the fork on GitHub, retrying if GitHub still processing"""
    rename_url = f"https://api.github.com/repos/{github_user}/{feature_name}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    payload = {"name": name}

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        click.secho(f"→ Renaming fork to '{name}' (attempt {attempt}/{max_attempts})...", fg="cyan")
        r = requests.patch(rename_url, headers=headers, json=payload)
        if r.status_code in (200, 201):
            new_clone_url = f"git@github.com:{github_user}/{name}.git"
            new_html_url = f"https://github.com/{github_user}/{name}"
            click.secho(f"✔ Fork renamed to {github_user}/{name}", fg="green")
            return new_clone_url, new_html_url
        if attempt < max_attempts:
            click.secho("⚠ GitHub still processing fork, waiting 3s before retry...", fg="yellow")
            time.sleep(3)
        else:
            click.secho(f"⚠ Could not rename fork after {max_attempts} attempts "
                        f"({r.status_code}: {r.text})", fg="yellow")
    return fork_clone_url, fork_html_url


def _clone_and_configure(feature_name, name, upstream_owner, fork_clone_url, cache_root):
    """Clone the fork locally and add the upstream remote"""
    local_path = os.path.join(cache_root, name)
    if os.path.exists(local_path):
        click.secho(f"✖ Local folder already exists: {local_path}", fg="red")
        raise SystemExit(2)

    click.secho(f"→ Cloning into {local_path} ...", fg="cyan")
    subprocess.run(["git", "clone", fork_clone_url, local_path], check=True)

    upstream_url = f"git@github.com:{upstream_owner}/{feature_name}.git"
    subprocess.run(["git", "-C", local_path, "remote", "add", "upstream", upstream_url], check=True)
    click.secho(f"✔ Added upstream remote: {upstream_url}", fg="green")

    return local_path


def _register_in_cache(cache_root: str, feature_name: str, repo_url: str):
    """Add or update entry in .splent_cache/features/index.txt"""
    index_path = os.path.join(cache_root, "index.txt")
    entry = f"{feature_name} {repo_url}\n"

    lines = []
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            lines = [l for l in f.readlines() if not l.startswith(feature_name + " ")]
    lines.append(entry)

    with open(index_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
