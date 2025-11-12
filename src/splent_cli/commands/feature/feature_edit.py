import os
import subprocess
import tomllib
import click
import socket


def _check_github_connectivity(host="github.com", port=443, timeout=3):
    """Check if there's an active network connection to GitHub."""
    try:
        socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False


@click.command("feature:edit")
@click.argument("feature_name", required=True)
def product_edit_feature(feature_name):
    """
    Prepare a feature for editing:
    - Checks write permissions.
    - Removes the version suffix.
    - Switches from detached HEAD to 'main' branch.
    - Updates pyproject.toml to remove the version from [project.optional-dependencies.features].
    """
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ Environment variable SPLENT_APP not set.")
        raise SystemExit(1)

    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product directory.")
        raise SystemExit(1)

    # --- Check internet connection before using git ---
    click.echo("🌐 Checking GitHub connectivity...")
    if not _check_github_connectivity():
        click.echo("❌ No connection to GitHub detected. Please check your network.")
        raise SystemExit(1)

    # Load pyproject
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = (
        data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    )
    if not features:
        click.echo("ℹ️ No features declared in pyproject.")
        raise SystemExit(1)

    # Find the feature
    match = next((f for f in features if feature_name in f), None)
    if not match:
        click.echo(f"❌ Feature {feature_name} not found in pyproject.")
        raise SystemExit(1)

    org, rest = match.split("/", 1) if "/" in match else ("splent-io", match)
    name, _, version = rest.partition("@")
    version = version or "v1.0.0"
    org_safe = org.replace("-", "_")

    cache_dir = os.path.join(
        workspace, ".splent_cache", "features", org_safe, f"{name}@{version}"
    )
    if not os.path.exists(cache_dir):
        click.echo(f"❌ Cached repository not found at {cache_dir}")
        raise SystemExit(1)

    click.echo(f"🧩 Editing feature: {org_safe}/{name}@{version}")

    # --- Check SSH write access ---
    try:
        subprocess.run(
            ["ssh", "-T", "git@github.com"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        can_push = True
    except Exception:
        can_push = False

    if not can_push:
        click.echo("⚠️ Could not verify SSH write access. Continuing anyway...")

    # --- Switch to editable branch ---
    os.chdir(cache_dir)
    # Fetch all branches
    subprocess.run(
        ["git", "fetch", "origin", "+refs/heads/*:refs/remotes/origin/*"], check=False
    )

    # Try to switch to main or create it if needed
    r = subprocess.run(
        ["git", "branch", "--list", "main"], capture_output=True, text=True
    )
    if not r.stdout.strip():
        subprocess.run(["git", "checkout", "-b", "main", "origin/main"], check=False)
    else:
        subprocess.run(["git", "switch", "main"], check=False)

    # Pull latest changes
    subprocess.run(["git", "pull", "origin", "main"], check=False)
    click.echo("🔄 Checked out to 'main' branch (editable mode).")

    # --- Rename cache folder ---
    new_dir = os.path.join(workspace, ".splent_cache", "features", org_safe, name)
    if new_dir != cache_dir:
        if os.path.exists(new_dir):
            click.echo(f"⚠️ {new_dir} already exists, skipping rename.")
        else:
            os.rename(cache_dir, new_dir)
            click.echo(f"📦 Renamed {cache_dir} → {new_dir}")

    # --- Update pyproject.toml (remove @version) ---
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    content = content.replace(f"{name}@{version}", name)

    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(content)

    click.echo(f"✅ Removed version from {name} in pyproject.toml.")

    # --- Recreate product symlink ---
    product_features_dir = os.path.join(product_path, "features", org_safe)
    os.makedirs(product_features_dir, exist_ok=True)

    old_link = os.path.join(product_features_dir, f"{name}@{version}")
    new_link = os.path.join(product_features_dir, name)

    if os.path.islink(old_link):
        os.unlink(old_link)
        click.echo(f"🧹 Removed old symlink {old_link}")

    target_path = os.path.join(workspace, ".splent_cache", "features", org_safe, name)
    if not os.path.exists(new_link):
        os.symlink(target_path, new_link)
        click.echo(f"🔗 Linked {new_link} → {target_path}")
    else:
        click.echo(f"✅ Symlink already up to date: {new_link}")

    click.echo("🎯 Feature ready for editing.")
