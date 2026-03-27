import os
import subprocess
import tomllib
import click
import requests
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.utils.cache_utils import make_feature_writable


# =====================================================================
# GITHUB WRITE ACCESS CHECK
# =====================================================================
def _has_write_access(ns_git: str, name: str) -> bool:
    """Return True if the authenticated user has push access to ns_git/name."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return False
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "User-Agent": "splent-cli",
    }
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{ns_git}/{name}",
            headers=headers,
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("permissions", {}).get("push", False)
    except requests.RequestException:
        pass
    return False


# =====================================================================
# CACHE PATH RESOLVER
# =====================================================================
def get_feature_paths(workspace: str, ns_fs: str, name: str, version: str | None):
    """Return (versioned_cache_path, editable_workspace_root_path)."""
    cache_base = os.path.join(workspace, ".splent_cache", "features", ns_fs)
    versioned = os.path.join(cache_base, f"{name}@{version}") if version else None
    editable = os.path.join(workspace, name)  # workspace root

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
    token = os.getenv("GITHUB_TOKEN")
    use_ssh = os.getenv("SPLENT_USE_SSH", "").lower() == "true"
    if use_ssh:
        remote_url = f"git@github.com:{ns_git}/{name}.git"
    elif token:
        remote_url = f"https://{token}@github.com/{ns_git}/{name}.git"
    else:
        remote_url = f"https://github.com/{ns_git}/{name}.git"
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
# HOT REINSTALL — pip install + Flask reload in the web container
# =====================================================================
def _hot_reinstall(workspace: str, product_path: str, editable_path: str, name: str):
    """Reinstall the feature via pip in the product's web container and trigger Flask reload."""
    product = os.path.basename(product_path)
    env = os.getenv("SPLENT_ENV", "dev")
    docker_dir = os.path.join(product_path, "docker")

    compose_file = compose.resolve_file(product_path, env)
    if not compose_file:
        return  # no docker-compose file — nothing to do

    pname = compose.project_name(product, env)
    container_id = compose.find_main_container(pname, compose_file, docker_dir)
    if not container_id:
        return  # container not running — nothing to do

    # 1. pip install -e from the new path
    click.echo(f"     🔄 Reinstalling {name} in web container...")
    pip_cmd = (
        f"pip install --no-cache-dir --root-user-action=ignore -q -e /workspace/{name}"
    )
    subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", pip_cmd],
        capture_output=True,
    )

    # 2. Touch the app's __init__.py to trigger watchmedo auto-restart
    init_py = f"/workspace/{product}/src/{product}/__init__.py"
    subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", f"touch {init_py}"],
        capture_output=True,
    )


# =====================================================================
# CORE LOGIC (single feature)
# =====================================================================
def _edit_one(
    workspace: str,
    product_path: str,
    pyproject_path: str,
    match: str,
    *,
    force: bool = False,
):
    """Convert one pyproject feature entry to editable. Returns True on success."""
    from splent_cli.utils.lifecycle import require_state, resolve_feature_key_from_entry

    _, ns_git, ns_fs, rest = compose.parse_feature_identifier(match)
    if "@" in rest:
        name, version = rest.split("@", 1)
    else:
        name, version = rest, None

    if not version:
        click.echo(f"  ℹ️  {match} — already editable, skipping.")
        return True

    # Guard: require write access to the GitHub repo
    if not _has_write_access(ns_git, name):
        click.secho(
            f"  ❌ No write access to {ns_git}/{name}.\n"
            f"     You cannot edit a feature you don't own.\n"
            f"     To work on your own copy, use: splent feature:fork {ns_git}/{name}",
            fg="red",
        )
        return False

    # Guard: cannot edit a feature with applied migrations
    key, _, _, _ = resolve_feature_key_from_entry(match)
    require_state(product_path, key, command="feature:edit", force=force)

    click.echo(f"  🧩 {ns_git}/{name}@{version}")

    versioned_path, editable_path = get_feature_paths(workspace, ns_fs, name, version)

    if not os.path.exists(versioned_path):
        click.secho(f"  ❌ Versioned cache not found: {versioned_path}", fg="red")
        return False

    if not os.path.exists(editable_path):
        click.echo(f"     📦 Creating editable copy → {editable_path}")
        subprocess.run(["rm", "-rf", editable_path], check=True)
        result = subprocess.run(["cp", "-r", versioned_path, editable_path])
        if result.returncode != 0:
            click.secho("     ❌ Failed to copy feature to editable path.", fg="red")
            return False
        # Unlock files so the editable copy is writable
        make_feature_writable(editable_path)

    ensure_git_main(editable_path, ns_git, name)
    replace_pyproject_reference(pyproject_path, name, version)

    product_features_dir = os.path.join(product_path, "features", ns_fs)
    os.makedirs(product_features_dir, exist_ok=True)

    old = os.path.join(product_features_dir, f"{name}@{version}")
    new = os.path.join(product_features_dir, name)

    if os.path.islink(old):
        os.unlink(old)
    # Always recreate the symlink to ensure it points to workspace root
    if os.path.islink(new):
        os.unlink(new)
    rel_target = os.path.relpath(editable_path, product_features_dir)
    os.symlink(rel_target, new)

    # Reinstall via pip in the product's web container so the running
    # Flask process picks up the new location without a manual restart.
    _hot_reinstall(workspace, product_path, editable_path, name)

    click.secho("     ✔  ready for editing.", fg="green")
    return True


# =====================================================================
# MAIN COMMAND
# =====================================================================
@click.command(
    "feature:edit",
    short_help="Convert a released feature into a local editable version.",
)
@click.argument("feature_name", required=False, default=None)
@click.option(
    "--all",
    "edit_all",
    is_flag=True,
    help="Convert all versioned features to editable.",
)
@click.option("--force", is_flag=True, help="Bypass lifecycle state checks.")
def feature_edit(feature_name, edit_all, force):
    workspace = str(context.workspace())
    product = context.require_app()

    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found.")
        raise SystemExit(1)

    if not feature_name and not edit_all:
        raise click.UsageError("Provide a <feature_name> or use --all.")

    if feature_name and edit_all:
        raise click.UsageError("Cannot use --all with a feature name.")

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = read_features_from_data(data)

    # ── Single feature ────────────────────────────────────────────────
    if feature_name:
        if "/" in feature_name:
            _, feature_name = feature_name.split("/", 1)

        match = next(
            (f for f in features if f.split("@")[0].split("/")[-1] == feature_name),
            None,
        )
        if not match:
            click.echo(f"❌ Feature {feature_name} not found in pyproject.")
            raise SystemExit(1)

        click.echo()
        _edit_one(workspace, product_path, pyproject_path, match, force=force)
        click.echo()
        return

    # ── All features ──────────────────────────────────────────────────
    versioned = [f for f in features if "@" in f.split("/")[-1]]
    already_editable = [f for f in features if "@" not in f.split("/")[-1]]

    click.echo()
    if already_editable:
        click.secho(
            f"  ℹ️  {len(already_editable)} feature(s) already editable — skipping.",
            fg="bright_black",
        )

    if not versioned:
        click.secho("  ✅ All features are already editable.", fg="green")
        click.echo()
        return

    click.secho(f"  Converting {len(versioned)} feature(s) to editable:\n", fg="cyan")
    ok = 0
    for match in versioned:
        if _edit_one(workspace, product_path, pyproject_path, match, force=force):
            ok += 1
        click.echo()

    click.secho(
        f"  {ok}/{len(versioned)} feature(s) converted.",
        fg="green" if ok == len(versioned) else "yellow",
    )
    click.echo()
