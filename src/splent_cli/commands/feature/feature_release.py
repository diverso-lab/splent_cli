import os
import re
import subprocess
import requests
import click
from splent_cli.commands.feature.feature_attach import feature_attach


@click.command("feature:release")
@click.argument("feature_name", required=True)
@click.argument("version", required=True)
@click.option("--attach", is_flag=True, help="Also attach this version to the current product")
def feature_release(feature_name, version, attach):
    """
    Create a Git tag, push, and GitHub release for a feature.
    Confirms and commits version bump before tagging.
    Must be executed from /workspace.
    """
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    org_safe = "splent_io"
    cache_base = os.path.join(workspace, ".splent_cache", "features", org_safe)

    candidates = [
        os.path.join(cache_base, f"{feature_name}@{version}"),
        os.path.join(cache_base, feature_name)
    ]
    feature_path = next((c for c in candidates if os.path.exists(c)), None)
    if not feature_path:
        click.echo(f"❌ Feature {feature_name} not found in cache.")
        raise SystemExit(1)

    os.chdir(feature_path)
    click.echo(f"🚀 Releasing {feature_name} version {version} from {feature_path}")

    # --- Update pyproject.toml version -------------------------------------
    py_path = os.path.join(feature_path, "pyproject.toml")
    if not os.path.exists(py_path):
        click.echo("❌ pyproject.toml not found in feature directory.")
        raise SystemExit(1)

    with open(py_path, "r", encoding="utf-8") as f:
        content = f.read()

    normalized_version = version.lstrip("v")
    content = re.sub(
        r'(?m)^version\s*=\s*["\'].*?["\']',
        f'version = "{normalized_version}"',
        content
    )

    with open(py_path, "w", encoding="utf-8") as f:
        f.write(content)

    click.echo(f"🧩 Updated pyproject.toml version → {normalized_version}")

    # --- Check local changes -----------------------------------------------
    r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if r.stdout.strip():
        click.echo("⚠️  Detected local changes:")
        click.echo(r.stdout.strip())
        if click.confirm("Do you want to commit and push these changes before releasing?", default=True):
            subprocess.run(["git", "add", "-A"], check=False)
            subprocess.run(["git", "commit", "-m", f"chore: bump version to {version}"], check=False)
            subprocess.run(["git", "push", "--set-upstream", "origin", "main"], check=False)
            click.echo("☁️  Changes committed and pushed to origin.")
        else:
            click.echo("🚫 Release cancelled (uncommitted changes).")
            raise SystemExit(1)
    else:
        click.echo("✅ Working tree clean, proceeding with release.")

    # --- Tag and push ------------------------------------------------------
    subprocess.run(["git", "fetch", "origin", "--tags"], check=False)
    existing_tags = subprocess.run(["git", "tag"], capture_output=True, text=True).stdout.splitlines()
    if version in existing_tags:
        click.echo(f"⚠️ Tag {version} already exists locally. Skipping tag creation.")
    else:
        subprocess.run(["git", "tag", "-a", version, "-m", f"Release {version}"], check=False)
        click.echo(f"🏷️  Tag {version} created.")

    subprocess.run(["git", "push", "origin", version], check=False)
    click.echo(f"☁️  Tag {version} pushed to origin.")

    # --- GitHub release ----------------------------------------------------
    remote_url = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                                capture_output=True, text=True).stdout.strip()
    repo = re.sub(r"^git@github\.com:|\.git$", "", remote_url)
    api_url = f"https://api.github.com/repos/{repo}/releases"
    token = os.getenv("GITHUB_TOKEN")

    if token:
        payload = {"tag_name": version, "name": version, "draft": False, "prerelease": False}
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        resp = requests.post(api_url, headers=headers, json=payload)
        if resp.status_code in (200, 201):
            click.echo(f"✅ GitHub release created: {resp.json().get('html_url')}")
        elif resp.status_code == 422 and "already_exists" in resp.text:
            click.echo(f"⚠️ GitHub release {version} already exists. Skipping.")
        else:
            click.echo(f"⚠️ Failed to create release: {resp.status_code} {resp.text}")
    else:
        click.echo("⚠️ GITHUB_TOKEN not set. Skipping GitHub release creation.")

    # --- Optionally call attach command ------------------------------------
    if attach:
        click.echo("🔗 Attaching version to product...")
        ctx = click.get_current_context()
        ctx.invoke(feature_attach, feature_name=feature_name, version=version)

    click.echo("🎉 Feature release completed.")
