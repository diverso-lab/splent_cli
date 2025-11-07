import os
import subprocess
import time
import click
import requests


@click.command("feature:fork", help="Fork a SPLENT feature on GitHub (remote only, no clone)")
@click.argument("feature_name", required=True)
@click.option("--version", "-v", default="v1.0.0", help="Feature version (default: v1.0.0)")
def feature_fork(feature_name, version):
    token = os.getenv("GITHUB_TOKEN")
    github_user = os.getenv("GITHUB_USER")

    if not token or not github_user:
        click.secho("✖ Error: GITHUB_TOKEN and GITHUB_USER must be set", fg="red")
        raise SystemExit(1)

    upstream_owner = "splent-io"
    api_url = f"https://api.github.com/repos/{upstream_owner}/{feature_name}/forks"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json"
    }

    click.secho(f"🔁 Forking {upstream_owner}/{feature_name} into {github_user}...", fg="cyan")

    resp = requests.post(api_url, headers=headers, json={"default_branch_only": False})
    if resp.status_code not in (201, 202):
        click.secho(f"❌ Failed: {resp.status_code} {resp.text}", fg="red")
        raise SystemExit(2)

    data = resp.json()
    fork_html_url = data["html_url"]
    click.secho(f"🌐 Fork created: {fork_html_url}", fg="green")

    # Espera a que GitHub termine de procesarlo
    for i in range(5):
        check_url = f"https://api.github.com/repos/{github_user}/{feature_name}"
        r = requests.get(check_url, headers=headers)
        if r.status_code == 200:
            click.secho("✅ Fork is ready.", fg="green")
            break
        click.secho("⌛ Waiting for GitHub to process fork...", fg="yellow")
        time.sleep(3)
    else:
        click.secho("⚠ Fork may still be processing.", fg="yellow")

    click.secho("\n🚀 Cloning fork into local namespace...\n", fg="cyan")
    ctx = click.get_current_context()
    ctx.invoke(feature_clone, feature_name=feature_name, version=version)


@click.command("feature:clone", help="Clone a forked SPLENT feature into your local cache namespace")
@click.argument("feature_name", required=True)
@click.option("--version", "-v", default="v1.0.0", help="Feature version (default: v1.0.0)")
def feature_clone(feature_name, version):
    """Clone your fork (on your GitHub account) into local cache under your namespace."""
    github_user = os.getenv("GITHUB_USER")
    workspace = os.getenv("WORKING_DIR", "/workspace")

    if not github_user:
        click.secho("✖ GITHUB_USER not set.", fg="red")
        raise SystemExit(1)

    # Ruta destino
    cache_root = os.path.join(workspace, ".splent_cache", "features")
    namespace_dir = os.path.join(cache_root, github_user.replace("-", "_"))
    os.makedirs(namespace_dir, exist_ok=True)

    local_path = os.path.join(namespace_dir, f"{feature_name}@{version}")
    if os.path.exists(local_path):
        click.secho(f"⚠️ Folder already exists: {local_path}", fg="yellow")
        return

    fork_url = f"git@github.com:{github_user}/{feature_name}.git"

    click.secho(f"⬇️ Cloning {fork_url}", fg="cyan")
    subprocess.run(["git", "clone", fork_url, local_path], check=True)

    click.secho(f"✅ Cloned into {local_path}", fg="green")