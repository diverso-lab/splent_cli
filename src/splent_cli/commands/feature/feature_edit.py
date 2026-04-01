import os
import subprocess
import tomllib
import click
import requests
from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.utils.cache_utils import make_feature_writable


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
    from splent_cli.utils.git_url import build_git_url

    remote_url, _ = build_git_url(ns_git, name)
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
# HOT REINSTALL — delegated to shared utility
# =====================================================================
def _hot_reinstall(workspace: str, product_path: str, editable_path: str, name: str):
    from splent_cli.utils.feature_utils import hot_reinstall

    hot_reinstall(product_path, f"/workspace/{name}", name)


# =====================================================================
# WEBPACK COMPILATION
# =====================================================================
def _compile_assets(workspace: str, product_path: str, name: str):
    """Compile webpack assets using the product's node_modules via the web container."""
    import shlex

    product = os.path.basename(product_path)
    env = os.getenv("SPLENT_ENV", "dev")

    # Find webpack config
    editable_path = os.path.join(workspace, name)
    webpack_file = None
    for root, dirs, files in os.walk(editable_path):
        if "webpack.config.js" in files:
            webpack_file = os.path.join(root, "webpack.config.js")
            break
    if not webpack_file:
        return

    # Find the web container
    compose_file = compose.resolve_file(product_path, env)
    if not compose_file:
        return
    docker_dir = os.path.join(product_path, "docker")
    pname = compose.project_name(product, env)
    container_id = compose.find_main_container(pname, compose_file, docker_dir)
    if not container_id:
        return

    click.echo(click.style("    compiling assets...", dim=True))
    product_root = f"/workspace/{product}"
    cmd = f"cd {shlex.quote(product_root)} && npx webpack --config {shlex.quote(webpack_file)} --mode development"
    result = subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", cmd],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        click.secho("    asset compilation failed.", fg="yellow")


# =====================================================================
# WRITE ACCESS WARNING
# =====================================================================
def _warn_no_push_access(ns_git: str, name: str):
    """Print a warning if the user cannot push to the repo. Non-blocking."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return  # no token = can't check, don't nag

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
            perms = resp.json().get("permissions", {})
            if not perms.get("push", False):
                click.secho(
                    f"    note: no push access to {ns_git}/{name}\n"
                    f"    use 'splent feature:fork' to work on your own copy",
                    fg="yellow",
                )
    except requests.RequestException:
        pass  # network error — don't block


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

    _, ns_git, ns_fs, rest = compose.parse_feature_identifier(match)
    if "@" in rest:
        name, version = rest.split("@", 1)
    else:
        name, version = rest, None

    if not version:
        click.echo(click.style(f"  {name}", dim=True) + " already editable")
        return True

    short = name.replace("splent_feature_", "")
    click.echo(f"  {short} ({version}) -> editable")

    versioned_path, editable_path = get_feature_paths(workspace, ns_fs, name, version)

    if not os.path.exists(versioned_path):
        click.secho(f"    cached version not found: {versioned_path}", fg="red")
        return False

    if not os.path.exists(editable_path):
        import shutil

        click.echo(click.style("    copying to workspace root...", dim=True))
        result = subprocess.run(["cp", "-r", versioned_path, editable_path])
        if result.returncode != 0:
            shutil.rmtree(editable_path, ignore_errors=True)
            click.secho("    failed to copy feature.", fg="red")
            return False

    # Always ensure editable copy is writable (cache files are read-only)
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
    rel_target = os.path.relpath(editable_path, product_features_dir)
    try:
        os.symlink(rel_target, new)
    except FileExistsError:
        os.unlink(new)
        os.symlink(rel_target, new)

    # Reinstall via pip in the product's web container so the running
    # Flask process picks up the new location without a manual restart.
    _hot_reinstall(workspace, product_path, editable_path, name)

    # Compile webpack assets via the product's web container
    _compile_assets(workspace, product_path, name)

    # Non-blocking warning if user can't push
    _warn_no_push_access(ns_git, name)

    click.secho("    ready.", fg="green")
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
        click.secho("  pyproject.toml not found.", fg="red")
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
            click.secho(f"  {feature_name} not found in pyproject.", fg="red")
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
        click.echo(
            click.style(
                f"  {len(already_editable)} feature(s) already editable, skipping.",
                dim=True,
            )
        )

    if not versioned:
        click.secho("  All features are already editable.", fg="green")
        click.echo()
        return

    click.echo(f"  Converting {len(versioned)} feature(s) to editable:")
    click.echo()
    ok = 0
    for match in versioned:
        if _edit_one(workspace, product_path, pyproject_path, match, force=force):
            ok += 1
        click.echo()

    click.secho(
        f"  {ok}/{len(versioned)} converted.",
        fg="green" if ok == len(versioned) else "yellow",
    )
    click.echo()
