import os
import subprocess
import time
import click
import requests


# ============================================================
# feature:fork
# ============================================================


@click.command("feature:fork", help="Fork a SPLENT feature on GitHub and clone it locally under your namespace")
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
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

    click.secho(f"🔁 Forking {upstream_owner}/{feature_name} into {github_user}...", fg="cyan")
    resp = requests.post(api_url, headers=headers, json={"default_branch_only": False})
    if resp.status_code not in (201, 202):
        click.secho(f"❌ Failed: {resp.status_code} {resp.text}", fg="red")
        raise SystemExit(2)

    fork_html_url = resp.json()["html_url"]
    click.secho(f"🌐 Fork created: {fork_html_url}", fg="green")

    # Esperar a que GitHub lo procese
    for i in range(5):
        r = requests.get(f"https://api.github.com/repos/{github_user}/{feature_name}", headers=headers)
        if r.status_code == 200:
            click.secho("✅ Fork is ready.", fg="green")
            break
        click.secho("⌛ Waiting for GitHub to process fork...", fg="yellow")
        time.sleep(3)
    else:
        click.secho("⚠ Fork may still be processing.", fg="yellow")

    # Llamar a clone automáticamente
    click.secho("\n🚀 Cloning fork into local namespace...\n", fg="cyan")
    ctx = click.get_current_context()
    ctx.invoke(feature_clone, feature_name=feature_name, version=version)

# ============================================================
# feature:clone
# ============================================================


@click.command("feature:clone", help="Clone a forked SPLENT feature into your local cache namespace")
@click.argument("feature_name", required=True)
@click.option("--version", "-v", default="v1.0.0", help="Feature version (default: v1.0.0)")
def feature_clone(feature_name, version):
    """Clone your fork into .splent_cache/features/<user> and fix namespace + symlink + push."""
    github_user = os.getenv("GITHUB_USER")
    workspace = os.getenv("WORKING_DIR", "/workspace")
    product = os.getenv("SPLENT_APP")

    if not github_user:
        click.secho("✖ GITHUB_USER not set.", fg="red")
        raise SystemExit(1)

    if not product:
        click.secho("✖ SPLENT_APP not set. Run 'splent select <product>' first.", fg="red")
        raise SystemExit(1)

    org_safe = github_user.lower().replace("-", "_").replace(".", "_")
    cache_root = os.path.join(workspace, ".splent_cache", "features", org_safe)
    os.makedirs(cache_root, exist_ok=True)

    local_path = os.path.join(cache_root, f"{feature_name}@{version}")
    if os.path.exists(local_path):
        click.secho(f"⚠️ Folder already exists: {local_path}", fg="yellow")
        return

    fork_url = f"git@github.com:{github_user}/{feature_name}.git"
    click.secho(f"⬇️ Cloning {fork_url} into {local_path}", fg="cyan")
    subprocess.run(["git", "clone", "--branch", version, "--depth", "1", fork_url, local_path], check=True)

    # === Namespace fix ===
    src_dir = os.path.join(local_path, "src")
    old_ns_dir = os.path.join(src_dir, "splent_io")
    new_ns_dir = os.path.join(src_dir, org_safe)
    modified = False

    if os.path.exists(old_ns_dir):
        click.secho(f"🧩 Adjusting namespace: splent_io → {org_safe}", fg="yellow")
        os.rename(old_ns_dir, new_ns_dir)
        modified = True

        for root, _, files in os.walk(new_ns_dir):
            for file in files:
                if file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    new_content = content.replace("splent_io.", f"{org_safe}.")
                    if new_content != content:
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(new_content)
                        modified = True
        if modified:
            click.secho("✅ Namespace adjusted successfully.", fg="green")
    else:
        click.secho("ℹ️ No splent_io namespace found — nothing to adjust.", fg="cyan")

    # === Commit y push si hubo cambios ===
    if modified:
        click.secho("💾 Committing and pushing namespace changes...", fg="cyan")
        subprocess.run(["git", "add", "."], cwd=local_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: adjust namespace to {org_safe}"],
            cwd=local_path,
            check=False,
        )
        subprocess.run(["git", "push"], cwd=local_path, check=False)
        click.secho("🚀 Changes pushed to your fork.", fg="green")
    else:
        click.secho("ℹ️ No changes to push.", fg="yellow")
