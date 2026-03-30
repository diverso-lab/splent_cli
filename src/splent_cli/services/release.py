"""
Shared release pipeline helpers.

Used by feature:release, product:release, release:cli, and release:framework.
All release commands delegate to these functions instead of reimplementing
git, PyPI, or GitHub logic.
"""

import json
import os
import re
import sys
import subprocess
import urllib.error
import urllib.request

import click
import requests


# ── Environment validation ────────────────────────────────────────────

def validate_release_env(*, require_pypi: bool = True, require_docker: bool = False):
    """Check that the required credentials are available.

    Warns (but continues) when GITHUB_TOKEN is missing.
    Aborts when PyPI or Docker Hub credentials are required but absent.
    """
    missing = []

    if not os.getenv("GITHUB_TOKEN"):
        click.secho("  warn: GITHUB_TOKEN not set — GitHub release will be skipped", fg="yellow")

    if require_pypi:
        if not (os.getenv("TWINE_USERNAME") or os.getenv("PYPI_USERNAME")):
            missing.append("TWINE_USERNAME or PYPI_USERNAME")
        if not (os.getenv("TWINE_PASSWORD") or os.getenv("PYPI_PASSWORD")):
            missing.append("TWINE_PASSWORD or PYPI_PASSWORD")

    if require_docker:
        if not os.getenv("DOCKERHUB_USERNAME"):
            missing.append("DOCKERHUB_USERNAME")
        if not os.getenv("DOCKERHUB_PASSWORD"):
            missing.append("DOCKERHUB_PASSWORD")

    if missing:
        click.secho("  Missing required environment variables:", fg="red")
        for m in missing:
            click.echo(f"    - {m}")
        click.echo()
        click.secho(
            "  Run 'splent tokens' for instructions on how to obtain them.",
            fg="bright_black",
        )
        raise SystemExit(1)


# ── Git helpers ───────────────────────────────────────────────────────

def extract_repo(remote_url: str) -> str:
    """Extract 'org/repo' from a GitHub remote URL (HTTPS or SSH)."""
    patterns = [
        r"https://[^@]+@github\.com/(?P<org>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"https://github\.com/(?P<org>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"git@github\.com:(?P<org>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        m = re.match(pattern, remote_url)
        if m:
            return f"{m.group('org')}/{m.group('repo')}"
    raise SystemExit(f"  error: cannot extract GitHub repo from URL: {remote_url}")


def get_repo_from_path(cwd: str) -> str:
    """Read the remote.origin.url from git config and extract org/repo."""
    remote_url = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True, cwd=cwd,
    ).stdout.strip()
    return extract_repo(remote_url)


# ── Version management ────────────────────────────────────────────────

def update_version(pyproject_path: str, version: str):
    """Replace the version field in a pyproject.toml file."""
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()
    new_content = re.sub(
        r'(?m)^version\s*=\s*["\'].*?["\']',
        f'version = "{version}"',
        content,
    )
    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    click.echo(f"  version  {version} written to pyproject.toml")


def commit_and_push(cwd: str, version: str, subject: str = "bump version"):
    """Stage all changes, commit, and push to origin/main.

    If the working tree is clean, does nothing.
    """
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=cwd,
    )
    if not r.stdout.strip():
        click.echo("  commit   working tree clean — nothing to commit")
        return

    try:
        subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: {subject} to {version}"],
            cwd=cwd, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=cwd, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho(f"  error: git operation failed: {e.stderr.strip() if e.stderr else e}", fg="red")
        click.secho("  The version was bumped but not committed. Fix and retry.", fg="yellow")
        raise SystemExit(1)

    click.echo("  commit   changes committed and pushed")


def create_and_push_tag(cwd: str, version: str):
    """Create an annotated tag and push it to origin."""
    tag = version if version.startswith("v") else f"v{version}"
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--tags"],
            cwd=cwd, check=True, capture_output=True,
        )
        tags = subprocess.run(
            ["git", "tag"],
            capture_output=True, text=True, cwd=cwd, check=True,
        ).stdout.splitlines()

        if tag not in tags:
            subprocess.run(
                ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
                cwd=cwd, check=True, capture_output=True,
            )
            click.echo(f"  tag      {tag} created")
        else:
            click.secho(f"  tag      {tag} already exists — skipping", fg="yellow")

        subprocess.run(
            ["git", "push", "origin", tag],
            cwd=cwd, check=True, capture_output=True,
        )
        click.echo(f"  tag      {tag} pushed to origin")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        click.secho(f"  error: git tag operation failed: {stderr}", fg="red")
        raise SystemExit(1)

    return tag


# ── GitHub release ────────────────────────────────────────────────────

def create_github_release(repo: str, version: str, token: str | None):
    """Create a GitHub Release via API."""
    if not token:
        click.secho("  github   skipped (no GITHUB_TOKEN)", fg="yellow")
        return

    tag = version if version.startswith("v") else f"v{version}"
    version_number = version.lstrip("v")
    package = repo.split("/")[-1]

    body = (
        f"## {tag}\n\n"
        f"Automated release by **SPLENT**.\n\n"
        f"| | |\n|---|---|\n"
        f"| Tag | `{tag}` |\n"
        f"| Package | `{package}` |\n"
        f"| Version | `{version_number}` |\n\n"
        f"### Install\n"
        f"```\npip install {package}=={version_number}\n```\n"
    )

    resp = requests.post(
        f"https://api.github.com/repos/{repo}/releases",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "tag_name": tag,
            "name": f"Release {tag}",
            "body": body,
            "draft": False,
            "prerelease": False,
        },
    )

    if resp.status_code in (200, 201):
        click.echo(f"  github   release created: {resp.json().get('html_url')}")
    elif resp.status_code == 422 and "already_exists" in resp.text:
        click.secho("  github   release already exists — skipping", fg="yellow")
    else:
        click.secho(f"  github   release failed: {resp.status_code} {resp.text}", fg="yellow")


# ── PyPI ──────────────────────────────────────────────────────────────

def build_and_upload_pypi(path: str):
    """Build the package and upload to PyPI."""
    click.echo("  pypi     building package...")
    subprocess.run(["rm", "-rf", "dist"], cwd=path)
    try:
        subprocess.run(
            [sys.executable, "-m", "build"],
            check=True, cwd=path, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho(f"  error: package build failed: {e.stderr.strip() if e.stderr else ''}", fg="red")
        raise SystemExit(1)

    click.echo("  pypi     uploading...")
    try:
        subprocess.run(
            [sys.executable, "-m", "twine", "upload", "dist/*"],
            env=os.environ.copy(),
            check=True, cwd=path, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho(f"  error: PyPI upload failed: {e.stderr.strip() if e.stderr else ''}", fg="red")
        click.secho("  The package was built in dist/ — upload manually: twine upload dist/*", fg="yellow")
        raise SystemExit(1)

    click.echo("  pypi     upload complete")


# ── Semver wizard ─────────────────────────────────────────────────────

def fetch_latest_tag(org: str, repo_name: str) -> str | None:
    """Return the highest semver tag from GitHub, or None.

    Fetches up to 100 tags and sorts by semver (not creation date).
    """
    token = os.getenv("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "splent-cli",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"token {token}"

    url = f"https://api.github.com/repos/{org}/{repo_name}/tags?per_page=100"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            batch = json.loads(resp.read().decode())
        if not batch:
            return None

        versions = []
        for tag in batch:
            name = tag.get("name", "")
            m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", name)
            if m:
                versions.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), name))
        if not versions:
            return batch[0]["name"]
        versions.sort(reverse=True)
        return versions[0][3]
    except Exception:
        return None


def bump(version_str: str, bump_type: str) -> str:
    """Return the next version string for the given bump type."""
    clean = version_str.lstrip("v")
    parts = clean.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise click.ClickException(
            f"Cannot parse version '{version_str}' — expected vMAJOR.MINOR.PATCH"
        )
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if bump_type == "major":
        return f"v{major + 1}.0.0"
    if bump_type == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def semver_wizard(org: str, repo_name: str) -> str:
    """Interactive wizard: fetch current version, offer bump choices, return chosen version."""
    click.echo()
    click.secho("  Fetching current version from GitHub...", fg="bright_black")
    current = fetch_latest_tag(org, repo_name)

    if current:
        click.echo(f"  Current version: {click.style(current, fg='cyan', bold=True)}")
    else:
        click.secho("  No tags found — this will be the first release.", fg="yellow")
        current = "v0.0.0"

    patch_v = bump(current, "patch")
    minor_v = bump(current, "minor")
    major_v = bump(current, "major")

    click.echo()
    click.echo(click.style("  Bump type:", bold=True))
    click.echo(
        f"    {click.style('[1]', bold=True)} patch  {click.style(patch_v, fg='green')}"
        f"   bug fixes, no new features"
    )
    click.echo(
        f"    {click.style('[2]', bold=True)} minor  {click.style(minor_v, fg='yellow')}"
        f"   new features, backward compatible"
    )
    click.echo(
        f"    {click.style('[3]', bold=True)} major  {click.style(major_v, fg='red')}"
        f"   breaking changes"
    )
    click.echo(
        f"    {click.style('[4]', bold=True)} cancel"
    )
    click.echo()

    choice = click.prompt(
        "  Choice",
        type=click.Choice(["1", "2", "3", "4"]),
        show_choices=False,
    )
    if choice == "4":
        raise SystemExit("  Release cancelled.")

    chosen = {"1": patch_v, "2": minor_v, "3": major_v}[choice]

    click.echo()
    click.echo(f"  Will release as {click.style(chosen, fg='cyan', bold=True)}")
    click.confirm("  Proceed?", abort=True)
    return chosen


# ── Orchestrator ──────────────────────────────────────────────────────

def run_release_pipeline(
    name: str,
    path: str,
    version: str,
    *,
    require_docker: bool = False,
    pre_commit_hook=None,
    post_pypi_hook=None,
):
    """Run the standard release pipeline.

    1. Validate environment
    2. Update version in pyproject.toml
    3. (optional) pre_commit_hook — e.g. contract update
    4. Commit and push
    5. Tag and push
    6. GitHub release
    7. PyPI build + upload
    8. (optional) post_pypi_hook — e.g. snapshot creation, Docker release

    Hooks receive (path, version) as arguments.
    """
    validate_release_env(require_pypi=True, require_docker=require_docker)

    normalized = version.lstrip("v")
    tag = f"v{normalized}"
    pyproject = os.path.join(path, "pyproject.toml")

    if not os.path.isdir(path):
        raise SystemExit(f"  error: directory not found: {path}")
    if not os.path.isfile(pyproject):
        raise SystemExit(f"  error: pyproject.toml not found in {path}")

    click.echo()
    click.echo(click.style(f"  Releasing {name} {tag}", bold=True))
    click.echo()

    update_version(pyproject, normalized)

    if pre_commit_hook:
        pre_commit_hook(path, normalized)

    commit_and_push(path, tag, subject=f"release {name}")
    create_and_push_tag(path, tag)

    repo = get_repo_from_path(path)
    create_github_release(repo, tag, os.getenv("GITHUB_TOKEN"))

    from splent_cli.commands.clear_cache import clean_build_artifacts
    clean_build_artifacts(path, quiet=True)

    build_and_upload_pypi(path)

    if post_pypi_hook:
        post_pypi_hook(path, normalized)

    click.echo()
    click.secho(f"  {name} {tag} released.", fg="green")
    click.echo()
