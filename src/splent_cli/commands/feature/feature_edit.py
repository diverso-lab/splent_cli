import os
import subprocess
import tomllib
import click
from splent_cli.services import context


WORKSPACE = str(context.workspace())
DEFAULT_ORG = "splent-io"         # GitHub org (con guión)
DEFAULT_NAMESPACE = "splent_io"   # Filesystem namespace (con guión → guión bajo)


# =====================================================================
# PARSER: [namespace/]name[@version]
# =====================================================================
def parse_feature(feature: str):
    """
    Devuelve:
        ns_git → splent-io
        ns_fs  → splent_io
        name   → splent_feature_auth
        version→ v1.0.4 o None
    """
    if "/" in feature:
        ns_git, rest = feature.split("/", 1)
    else:
        ns_git = DEFAULT_ORG
        rest = feature

    if "@" in rest:
        name, version = rest.split("@", 1)
    else:
        name = rest
        version = None

    ns_fs = ns_git.replace("-", "_")
    return ns_git, ns_fs, name, version


# =====================================================================
# CACHE PATH RESOLVER
# =====================================================================
def get_cache_paths(ns_git: str, name: str, version: str | None):
    ns_fs = ns_git.replace("-", "_")
    base = os.path.join(WORKSPACE, ".splent_cache", "features", ns_fs)

    versioned = os.path.join(base, f"{name}@{version}") if version else None
    editable = os.path.join(base, name)

    return versioned, editable


# =====================================================================
# GIT helpers — bypass safe.directory via -c flag (Docker UID mismatch)
# =====================================================================
def _git(path: str, *args, capture: bool = False):
    return subprocess.run(
        ["git", "-C", path, "-c", "safe.directory=*", *args],
        capture_output=capture,
        text=capture,
    )


def _git_check(path: str, *args):
    return subprocess.run(
        ["git", "-C", path, "-c", "safe.directory=*", *args],
        check=True,
    )


def _git_out(path: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", path, "-c", "safe.directory=*", *args],
        capture_output=True,
        text=True,
    )


# =====================================================================
# GIT → ensure main branch editable
# =====================================================================
def ensure_git_main(path: str, ns_git: str, name: str):
    remote_url = f"git@github.com:{ns_git}/{name}.git"
    r = _git_out(path, "remote", "get-url", "origin")
    if r.returncode == 0:
        _git_check(path, "remote", "set-url", "origin", remote_url)
    else:
        _git_check(path, "remote", "add", "origin", remote_url)

    _git_check(path, "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*")

    r = _git_out(path, "branch", "--list", "main")
    if not r.stdout.strip():
        _git_check(path, "checkout", "-b", "main", "origin/main")
    else:
        _git_check(path, "switch", "main")

    _git_check(path, "pull", "origin", "main")


# =====================================================================
# PYPROJECT UPDATE
# =====================================================================
def replace_pyproject_reference(pyproject_path: str, name: str, version: str):
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    updated = content.replace(f"{name}@{version}", name)

    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(updated)


# =====================================================================
# MAIN COMMAND
# =====================================================================
@click.command(
    "feature:edit",
    short_help="Convert a released feature into a local editable version.",
)
@click.argument("feature_name")
def feature_edit(feature_name):
    product = context.require_app()

    product_path = os.path.join(WORKSPACE, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found.")
        raise SystemExit(1)

    # Load pyproject
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = data["project"]["optional-dependencies"]["features"]

    # Normalizar: si el usuario pasó splent_io/name, nos quedamos con name
    if "/" in feature_name:
        _, feature_name = feature_name.split("/", 1)

    # Buscar por nombre real, sin namespace
    match = next((f for f in features if f.split("@")[0] == feature_name), None)

    if not match:
        click.echo(f"❌ Feature {feature_name} not found in pyproject.")
        raise SystemExit(1)

    ns_git, ns_fs, name, version = parse_feature(match)

    if not version:
        click.echo("ℹ️ Feature already editable.")
        return

    click.echo(f"🧩 Editing feature {ns_git}/{name}@{version}")

    versioned_path, editable_path = get_cache_paths(ns_git, name, version)

    if not os.path.exists(versioned_path):
        click.echo(f"❌ Versioned cache not found: {versioned_path}")
        raise SystemExit(1)

    # Create editable copy only if missing (clean up partial previous attempt first)
    if not os.path.exists(editable_path):
        click.echo(f"📦 Creating editable copy → {editable_path}")
        subprocess.run(["rm", "-rf", editable_path], check=True)
        result = subprocess.run(["cp", "-r", versioned_path, editable_path])
        if result.returncode != 0:
            click.echo("❌ Failed to copy feature to editable path.")
            raise SystemExit(1)

    # Ensure Git repo points to main branch and right remote
    ensure_git_main(editable_path, ns_git, name)

    # Update pyproject to remove @version
    replace_pyproject_reference(pyproject_path, name, version)

    # Fix symlink
    product_features_dir = os.path.join(product_path, "features", ns_fs)
    os.makedirs(product_features_dir, exist_ok=True)

    old = os.path.join(product_features_dir, f"{name}@{version}")
    new = os.path.join(product_features_dir, name)

    if os.path.islink(old):
        os.unlink(old)

    if not os.path.islink(new):
        os.symlink(editable_path, new)

    click.echo("🎯 Feature ready for editing (editable, main branch, correct origin).")
