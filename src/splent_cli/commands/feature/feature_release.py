import os
import re
import sys
import subprocess
import requests
import click
from splent_cli.commands.feature.feature_attach import feature_attach
from splent_cli.services import context


DEFAULT_NAMESPACE = os.getenv('SPLENT_DEFAULT_NAMESPACE', 'splent_io')


# =====================================================================
# PARSER: [namespace/]name[@version]
# =====================================================================
def parse_feature_ref(ref: str, default_ns: str = DEFAULT_NAMESPACE):
    m = re.match(r"^(?:(?P<ns>[^/@]+)/)?(?P<name>[^@]+?)(?:@(?P<ver>.+))?$", ref)
    if not m:
        raise ValueError(f"❌ Invalid feature format: {ref}")

    ns = m.group("ns") or default_ns
    name = m.group("name")
    ver = m.group("ver")
    return ns, name, ver


# =====================================================================
# EXTRACT REPO NAME (org/repo)
# =====================================================================
def extract_repo(remote_url: str) -> str:

    # HTTPS with token
    m = re.match(r"https://[^@]+@github\.com/(?P<org>[^/]+)/(?P<repo>.+?)\.git$", remote_url)
    if m:
        return f"{m.group('org')}/{m.group('repo')}"

    # HTTPS normal
    m = re.match(r"https://github\.com/(?P<org>[^/]+)/(?P<repo>.+?)\.git$", remote_url)
    if m:
        return f"{m.group('org')}/{m.group('repo')}"

    # SSH
    m = re.match(r"git@github\.com:(?P<org>[^/]+)/(?P<repo>.+?)\.git$", remote_url)
    if m:
        return f"{m.group('org')}/{m.group('repo')}"

    raise SystemExit(f"❌ Cannot extract GitHub repo from: {remote_url}")


# =====================================================================
# VALIDACIÓN ENV
# =====================================================================
def validate_environment():
    missing = []

    if not os.getenv("SPLENT_APP"):
        missing.append("SPLENT_APP")

    if not os.getenv("GITHUB_TOKEN"):
        click.echo("⚠️ Warning: GITHUB_TOKEN not set → GitHub release will be skipped.")

    pypi_user = os.getenv("TWINE_USERNAME") or os.getenv("PYPI_USERNAME")
    pypi_pass = os.getenv("TWINE_PASSWORD") or os.getenv("PYPI_PASSWORD")

    if not pypi_user:
        missing.append("TWINE_USERNAME or PYPI_USERNAME")

    if not pypi_pass:
        missing.append("TWINE_PASSWORD or PYPI_PASSWORD")

    if missing:
        click.echo("❌ Missing required environment variables:")
        for m in missing:
            click.echo(f"   - {m}")
        raise SystemExit(1)


# =====================================================================
# LOCALIZAR CARPETA EDITABLE (base sin versión)
# =====================================================================
def resolve_feature_path(feature_ref: str, version_arg: str, workspace: str):
    ns, name, ver_in_ref = parse_feature_ref(feature_ref)

    if ver_in_ref:
        raise SystemExit(
            f"❌ Cannot release a versioned reference: '{feature_ref}'.\n"
            f"   Use: {ns}/{name}"
        )

    cache_base = os.path.join(workspace, ".splent_cache", "features", ns.replace("-", "_"))
    base_dir = os.path.join(cache_base, name)

    if not os.path.exists(base_dir):
        raise SystemExit(
            f"❌ Editable feature not found at:\n"
            f"   {base_dir}\n\n"
            f"Run: splent feature:clone {ns}/{name}"
        )

    return base_dir, ns, name, version_arg.lstrip("v")


# =====================================================================
# UPDATE VERSION
# =====================================================================
def update_version(py_path, normalized):
    import re
    with open(py_path, "r", encoding="utf-8") as f:
        content = f.read()

    new_content = re.sub(
        r'(?m)^version\s*=\s*["\'].*?["\']',
        f'version = "{normalized}"',
        content,
    )
    with open(py_path, "w", encoding="utf-8") as f:
        f.write(new_content)


# =====================================================================
# COMMIT
# =====================================================================
def commit_local_changes(version):
    r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)

    if not r.stdout.strip():
        click.echo("✅ Working tree clean.")
        return

    click.echo("⚠️ Local changes detected:")
    click.echo(r.stdout.strip())

    if not click.confirm("Commit and push?", default=True):
        raise SystemExit("🚫 Release cancelled.")

    subprocess.run(["git", "add", "-A"])
    subprocess.run(["git", "commit", "-m", f"chore: bump version to {version}"])
    subprocess.run(["git", "push", "origin", "main"])

    click.echo("☁️ Changes committed and pushed.")


# =====================================================================
# TAG
# =====================================================================
def create_and_push_git_tag(version):
    subprocess.run(["git", "fetch", "origin", "--tags"])

    tags = subprocess.run(["git", "tag"], capture_output=True, text=True).stdout.splitlines()

    if version not in tags:
        subprocess.run(["git", "tag", "-a", version, "-m", f"Release {version}"])
        click.echo(f"🏷️ Tag {version} created.")
    else:
        click.echo("⚠️ Tag already exists locally.")

    subprocess.run(["git", "push", "origin", version])
    click.echo("☁️ Tag pushed.")


# =====================================================================
# GITHUB RELEASE
# =====================================================================
def create_github_release(repo, version, token):
    """
    Create a polished GitHub Release with rich Markdown formatting.
    """
    if not token:
        click.echo("⚠️ No GITHUB_TOKEN → skipping release.")
        return

    api_url = f"https://api.github.com/repos/{repo}/releases"

    version_number = version.lstrip("v")
    package = repo.split("/")[-1]

    # BLOQUE SEGURO → triple comillas, pero sin usar backticks ``` dentro del f-string
    body = (
        f"## 🎉 {version}\n\n"
        f"Automated release generated by **SPLENT**.\n\n"
        f"### 📦 Details\n"
        f"- **Tag:** `{version}`\n"
        f"- **Repository:** `{repo}`\n"
        f"- **Version:** `{version_number}`\n\n"
        f"### 📥 Installation (PyPI)\n"
        f"```\n"
        f"pip install {package}=={version_number}\n"
        f"```\n\n"
        f"### 🧩 Notes\n"
        f"This release was automatically tagged, packaged and published by the **SPLENT pipeline**.\n\n"
        f"---\n"
    )

    payload = {
        "tag_name": version,
        "name": f"Release {version}",
        "body": body,
        "draft": False,
        "prerelease": False,
    }

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }

    resp = requests.post(api_url, headers=headers, json=payload)

    if resp.status_code in (200, 201):
        click.echo(f"✅ GitHub release created: {resp.json().get('html_url')}")
        return

    if resp.status_code == 422 and "already_exists" in resp.text:
        click.echo("⚠️ Release already exists. Skipping.")
        return

    click.echo(f"⚠️ Release failed: {resp.status_code} {resp.text}")



# =====================================================================
# PYPI
# =====================================================================
def build_and_upload_pypi(feature_path):
    os.chdir(feature_path)

    click.echo("📦 Building package...")
    subprocess.run(["rm", "-rf", "dist"])
    subprocess.run([sys.executable, "-m", "build"], check=True)

    env = os.environ.copy()

    click.echo("📤 Uploading to PyPI...")
    subprocess.run(
        [sys.executable, "-m", "twine", "upload", "dist/*"],
        env=env,
        check=True
    )
    click.echo("✅ PyPI upload complete.")


# =====================================================================
# SNAPSHOT VERSIONADO EN CACHÉ
# =====================================================================
def create_versioned_snapshot(namespace, feature_name, version, workspace):

    # Namespace filesystem: splent_io
    # Namespace GitHub: splent-io
    org_github = namespace.replace("_", "-")

    cache_root = os.path.join(workspace, ".splent_cache", "features", namespace)
    snapshot_path = os.path.join(cache_root, f"{feature_name}@{version}")

    clone_url = f"git@github.com:{org_github}/{feature_name}.git"

    click.echo(f"📥 Creating versioned snapshot: {snapshot_path}")
    click.echo(f"🔗 GitHub repo: {clone_url}")

    subprocess.run(
        ["git", "clone", "--branch", version, "--depth", "1", clone_url, snapshot_path],
        check=True
    )

    click.echo("✅ Snapshot created.")



# =====================================================================
# COMMAND
# =====================================================================
@click.command(
    "feature:release",
    short_help="Release a feature: bump version, tag, publish to GitHub/PyPI, and snapshot."
)
@click.argument("feature_ref")
@click.argument("version")
@click.option("--attach", is_flag=True)
def feature_release(feature_ref, version, attach):

    validate_environment()

    workspace = str(context.workspace())

    feature_path, namespace, feature_name, normalized = (
        resolve_feature_path(feature_ref, version, workspace)
    )

    os.chdir(feature_path)

    click.echo(f"🚀 Releasing {namespace}/{feature_name}@{version}")

    # Update pyproject + commit
    update_version(os.path.join(feature_path, "pyproject.toml"), normalized)
    commit_local_changes(version)

    # Tag + GitHub release
    create_and_push_git_tag(version)
    repo = extract_repo(subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True
    ).stdout.strip())
    create_github_release(repo, version, os.getenv("GITHUB_TOKEN"))

    # PyPI
    build_and_upload_pypi(feature_path)

    # Versioned snapshot
    create_versioned_snapshot(namespace, feature_name, version, workspace)

    # Optional attach
    if attach:
        click.echo("🔗 Attaching to product...")
        ctx = click.get_current_context()
        ctx.invoke(feature_attach, feature_identifier=feature_ref, version=version)

    click.echo("🎉 Release completed!")
