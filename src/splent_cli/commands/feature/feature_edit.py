import os
import subprocess
import shutil
import tomllib
import click


WORKSPACE = "/workspace"
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
# GIT → ensure main branch editable
# =====================================================================
def ensure_git_main(path: str, ns_git: str, name: str):
    os.chdir(path)

    # Fix remote origin to correct namespace + repo
    subprocess.run(
        ["git", "remote", "set-url", "origin", f"git@github.com:{ns_git}/{name}.git"],
        check=True,
    )

    subprocess.run(
        ["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"],
        check=True,
    )

    r = subprocess.run(["git", "branch", "--list", "main"], capture_output=True, text=True)

    if not r.stdout.strip():
        subprocess.run(["git", "checkout", "-b", "main", "origin/main"], check=True)
    else:
        subprocess.run(["git", "switch", "main"], check=True)

    subprocess.run(["git", "pull", "origin", "main"], check=True)


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
@click.argument("feature_name")
def feature_edit(feature_name):
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    product_path = os.path.join(WORKSPACE, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found.")
        raise SystemExit(1)

    # Load pyproject
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = data["project"]["optional-dependencies"]["features"]

    # Soporta:
    # - splent_feature_auth
    # - splent-io/splent_feature_auth
    # porque hacemos startswith(feature_name)
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

    # Create editable copy only if missing
    if not os.path.exists(editable_path):
        click.echo(f"📦 Creating editable copy → {editable_path}")
        shutil.copytree(versioned_path, editable_path)

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
