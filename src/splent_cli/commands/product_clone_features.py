import os
import subprocess
import tomllib

import click


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


@click.command("product:clone-features")
def product_clone_features():
    """Clona las features declaradas en pyproject.toml y crea los enlaces simbólicos."""
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
            subprocess.run(["git", "clone", "--branch", version, "--depth", "1", url, cache_dir], check=False)
        else:
            click.echo(f"✅ Using cached {org_safe}/{name}@{version}")

        product_features_dir = os.path.join(product_path, "features", org_safe)
        os.makedirs(product_features_dir, exist_ok=True)
        link_path = os.path.join(product_features_dir, f"{name}@{version}")

        if not os.path.exists(link_path):
            os.symlink(cache_dir, link_path)
            click.echo(f"🔗 Linked {name}@{version} → {cache_dir}")
