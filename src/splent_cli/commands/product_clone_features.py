import os
import subprocess
import tomllib
import click
import socket


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


def _check_github_connectivity(host="github.com", port=443, timeout=3):
    """Check if there's an active network connection to GitHub."""
    try:
        socket.create_connection((host, port), timeout=timeout)
        return True
    except OSError:
        return False


@click.command("product:clone-features")
def product_clone_features():
    """Clone features declared in pyproject.toml and create symbolic links."""
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    product_path = _get_product_path(product, workspace)

    def is_splent_developer():
        if os.getenv("SPLENT_USE_SSH", "").lower() == "true":
            return True
        if os.getenv("SPLENT_ROLE", "").lower() == "developer":
            return True
        try:
            r = subprocess.run(
                ["ssh", "-T", "git@github.com"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3
            )
            return "successfully authenticated" in r.stderr.lower()
        except Exception:
            return False

    # 1️⃣ Check connectivity before doing anything
    click.echo("🌐 Checking GitHub connectivity...")
    if not _check_github_connectivity():
        click.echo("❌ No connection to GitHub detected. Please check your network.")
        raise SystemExit(1)

    py = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py):
        click.echo(f"❌ pyproject.toml not found at {product_path}")
        return

    with open(py, "rb") as f:
        data = tomllib.load(f)

    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not features:
        click.echo("ℹ️ No features declared under [project.optional-dependencies.features]")
        return

    use_ssh = is_splent_developer()
    click.echo(f"🔑 Cloning mode: {'SSH' if use_ssh else 'HTTPS'}")

    cache_base = os.path.join(workspace, ".splent_cache", "features")
    os.makedirs(cache_base, exist_ok=True)

    for feature_entry in features:
        org, rest = feature_entry.split("/", 1) if "/" in feature_entry else ("splent-io", feature_entry)
        name, _, version = rest.partition("@")
        version = version or "v1.0.0"
        org_safe = org.replace("-", "_")

        click.echo(f"🔍 Feature: {name}@{version} (org: {org_safe})")
        url = f"git@github.com:{org}/{name}.git" if use_ssh else f"https://github.com/{org}/{name}.git"

        cache_dir = os.path.join(cache_base, org_safe, f"{name}@{version}")

        if not os.path.exists(cache_dir):
            try:
                # --- Clone only the main branch (avoids detached HEAD) ---
                subprocess.run(
                    ["git", "clone", "--depth", "1", url, cache_dir],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                # --- Fetch all tags and try to checkout the requested version ---
                subprocess.run(["git", "-C", cache_dir, "fetch", "--tags"], check=False)
                tag_result = subprocess.run(
                    ["git", "-C", cache_dir, "tag", "-l", version],
                    capture_output=True,
                    text=True
                )

                if tag_result.stdout.strip() == version:
                    subprocess.run(["git", "-C", cache_dir, "checkout", version], check=False)
                    click.echo(f"🏷️  Checked out tag {version}")
                else:
                    click.echo(f"⚠️ Tag {version} not found — staying on main branch")

            except subprocess.CalledProcessError as e:
                click.echo(f"❌ Failed to clone {name}@{version}: {e.stderr.strip()}")
                raise SystemExit(1)
        else:
            click.echo(f"✅ Using cached {org_safe}/{name}@{version}")

        # --- Create symlink to product ---
        product_features_dir = os.path.join(product_path, "features", org_safe)
        os.makedirs(product_features_dir, exist_ok=True)
        link_path = os.path.join(product_features_dir, f"{name}@{version}")

        if not os.path.exists(link_path):
            os.symlink(cache_dir, link_path)
            click.echo(f"🔗 Linked {name}@{version} → {cache_dir}")

    click.echo("🎉 All features cloned and linked successfully.")
