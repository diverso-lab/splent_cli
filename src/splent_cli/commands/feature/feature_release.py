import os
import re
import sys
import shutil
import subprocess
import requests
import click
from splent_cli.commands.feature.feature_attach import feature_attach


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
    ver = m.group("ver")  # optional

    return ns, name, ver


def extract_repo(remote_url: str) -> str:
    """
    Normalize any git remote URL (SSH or HTTPS, with or without embedded tokens)
    into GitHub API format: org/repo
    """

    # 1) HTTPS with embedded token → https://TOKEN@github.com/org/repo.git
    m = re.match(
        r"https://[^@]+@github\.com/(?P<org>[^/]+)/(?P<repo>.+?)\.git$",
        remote_url
    )
    if m:
        return f"{m.group('org')}/{m.group('repo')}"

    # 2) Standard HTTPS → https://github.com/org/repo.git
    m = re.match(
        r"https://github\.com/(?P<org>[^/]+)/(?P<repo>.+?)\.git$",
        remote_url
    )
    if m:
        return f"{m.group('org')}/{m.group('repo')}"

    # 3) SSH → git@github.com:org/repo.git
    m = re.match(
        r"git@github\.com:(?P<org>[^/]+)/(?P<repo>.+?)\.git$",
        remote_url
    )
    if m:
        return f"{m.group('org')}/{m.group('repo')}"

    raise SystemExit(f"❌ Cannot extract GitHub repo from: {remote_url}")


# =====================================================================
# VALIDACIÓN DE VARIABLES DE ENTORNO
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
# LOCALIZAR CARPETA BASE
# =====================================================================
def resolve_feature_path(feature_ref: str, version_arg: str, workspace: str):
    ns, name, ver_in_ref = parse_feature_ref(feature_ref)

    # ❌ Prohibido hacer release de referencias versionadas
    if ver_in_ref:
        raise SystemExit(
            f"❌ Cannot release a versioned reference: '{feature_ref}'.\n"
            f"   Use the base feature name: {ns}/{name}"
        )

    org_safe = ns.replace("-", "_")
    cache_base = os.path.join(workspace, ".splent_cache", "features", org_safe)

    # ✔ Solo permitimos carpeta base sin versión
    candidate_base = os.path.join(cache_base, name)
    if os.path.exists(candidate_base):
        return candidate_base, ns, name, version_arg.lstrip("v")

    raise SystemExit(
        f"❌ Cannot release feature '{name}' because base folder does not exist.\n"
        f"   Required path:\n"
        f"   {candidate_base}\n\n"
        f"   Run: splent feature:clone {ns}/{name}   (WITHOUT version)\n"
        f"   to create an editable base clone."
    )


# =====================================================================
# UPDATE VERSION IN PYPORJECT.TOML
# =====================================================================
def update_version(py_path, normalized):
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
# COMMIT LOCAL
# =====================================================================
def commit_local_changes(version):
    r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if not r.stdout.strip():
        click.echo("✅ Working tree clean.")
        return

    click.echo("⚠️ Local changes detected:")
    click.echo(r.stdout.strip())

    if not click.confirm("Commit and push changes?", default=True):
        raise SystemExit("🚫 Release cancelled.")

    subprocess.run(["git", "add", "-A"])
    subprocess.run(["git", "commit", "-m", f"chore: bump version to {version}"])
    subprocess.run(["git", "push", "origin", "main"])

    click.echo("☁️ Changes committed and pushed.")


# =====================================================================
# TAG + PUSH
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
    if not token:
        click.echo("⚠️ Skipping GitHub release (no GITHUB_TOKEN).")
        return

    api_url = f"https://api.github.com/repos/{repo}/releases"

    # Detalles del release
    title = f"Release {version}"
    body = (
        f"## 🎉 {version}\n"
        f"Automated release generated by SPLENT.\n\n"
        f"- Tag: `{version}`\n"
        f"- Repository: `{repo}`\n\n"
        f"Use `pip install {repo.split('/')[-1]}=={version.lstrip('v')}` to install this feature.\n"
    )

    payload = {
        "tag_name": version,
        "name": title,
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

    click.echo(f"⚠️ GitHub release failed: {resp.status_code} {resp.text}")



# =====================================================================
# PYPI BUILD + UPLOAD
# =====================================================================
def build_and_upload_pypi(feature_path):
    os.chdir(feature_path)

    click.echo("📦 Building package...")
    subprocess.run(["rm", "-rf", "dist"])
    subprocess.run([sys.executable, "-m", "build"], check=True)

    user = os.getenv("TWINE_USERNAME") or os.getenv("PYPI_USERNAME")
    pwd = os.getenv("TWINE_PASSWORD") or os.getenv("PYPI_PASSWORD")

    env = os.environ.copy()
    env["TWINE_USERNAME"] = user
    env["TWINE_PASSWORD"] = pwd

    click.echo("📤 Uploading to PyPI...")
    subprocess.run([sys.executable, "-m", "twine", "upload", "dist/*"], env=env, check=True)

    click.echo("✅ PyPI upload complete.")


# =====================================================================
# MAIN COMMAND
# =====================================================================
@click.command("feature:release")
@click.argument("feature_ref")
@click.argument("version")
@click.option("--attach", is_flag=True)
def feature_release(feature_ref, version, attach):
    validate_environment()

    workspace = "/workspace"

    feature_path, namespace, feature_name, normalized = resolve_feature_path(
        feature_ref, version, workspace
    )

    # Entrar en la feature base
    os.chdir(feature_path)
    click.echo(f"🚀 Releasing {namespace}/{feature_name}@{version}")

    # Actualizar pyproject y commit
    py_path = os.path.join(feature_path, "pyproject.toml")
    update_version(py_path, normalized)
    commit_local_changes(version)

    # Tag & Push
    create_and_push_git_tag(version)

    # GitHub release
    remote_url = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True
    ).stdout.strip()

    remote_url = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True, text=True
    ).stdout.strip()

    repo = extract_repo(remote_url)

    create_github_release(repo, version, os.getenv("GITHUB_TOKEN"))

    # PyPI upload
    build_and_upload_pypi(feature_path)

    # =====================================================================
    # REFRESH CACHE BASE AFTER SUCCESSFUL RELEASE
    # =====================================================================
    cache_base_root = os.path.join(
        workspace, ".splent_cache", "features", namespace.replace("-", "_")
    )
    base_path = os.path.join(cache_base_root, feature_name)

    click.echo("♻️ Refreshing base cache...")

    if os.path.exists(base_path):
        shutil.rmtree(base_path)

    # namespace_raw = namespace tal como viene de GitHub (con guion)
    namespace_raw = namespace      # splent-io
    namespace_safe = namespace.replace("-", "_")  # splent_io

    cache_base_root = os.path.join(
        workspace, ".splent_cache", "features", namespace_safe
    )
    base_path = os.path.join(cache_base_root, feature_name)

    # 👉 usar SIEMPRE namespace_raw para GitHub
    clone_url = f"git@github.com:{namespace_raw}/{feature_name}.git"

    subprocess.run(["git", "clone", clone_url, base_path], check=True)

    click.echo("✅ Base cache updated with latest release.")

    # Attaching to product
    if attach:
        click.echo("🔗 Attaching to current product...")
        ctx = click.get_current_context()
        ctx.invoke(feature_attach, feature_name=feature_ref, version=version)

    click.echo("🎉 Release completed!")
