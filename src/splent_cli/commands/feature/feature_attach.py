import os
import re
import subprocess
import click
import requests


@click.command("feature:attach")
@click.argument("feature_name", required=True)
@click.argument("version", required=True)
def feature_attach(feature_name, version):
    """
    Attach a released feature version to the current product:
    - Verify the GitHub tag exists
    - Clone the repo if missing in cache
    - Update pyproject.toml in the product
    - Rename cache folder
    - Update symlink under /workspace/<product>/features
    """
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    org = "splent-io"
    org_safe = org.replace("-", "_")
    cache_base = os.path.join(workspace, ".splent_cache", "features", org_safe)
    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product.")
        raise SystemExit(1)

    # --- 1️⃣ Verify that the GitHub tag exists -------------------------------
    click.echo(f"🔍 Checking GitHub for {feature_name}@{version}...")

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "splent-cli"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    tag_api_urls = [
        f"https://api.github.com/repos/{org}/{feature_name}/git/refs/tags/{version}",
        f"https://api.github.com/repos/{org}/{feature_name}/releases/tags/{version}",
    ]
    html_url = f"https://github.com/{org}/{feature_name}/releases/tag/{version}"

    exists = False
    for url in tag_api_urls:
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                exists = True
                break
        except requests.RequestException:
            pass

    # Fallback: public HTML (works even when API 404s)
    if not exists:
        try:
            resp = requests.get(html_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                exists = True
        except requests.RequestException:
            pass

    if exists:
        click.echo(f"✅ GitHub tag {version} exists.")
    else:
        click.echo(f"❌ Tag {version} not found for {org}/{feature_name} on GitHub.")
        raise SystemExit(1)

    # --- 2️⃣ Clone the feature if not cached ---------------------------------
    versioned_dir = os.path.join(cache_base, f"{feature_name}@{version}")
    if not os.path.exists(versioned_dir):
        click.echo(f"⬇️  Cloning {feature_name}@{version}...")
        use_ssh = os.getenv("SPLENT_USE_SSH", "").lower() == "true"
        url = (
            f"git@github.com:{org}/{feature_name}.git"
            if use_ssh
            else f"https://github.com/{org}/{feature_name}.git"
        )
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--branch",
                    version,
                    "--depth",
                    "1",
                    url,
                    versioned_dir,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            click.echo(f"✅ Feature cloned to {versioned_dir}")
        except subprocess.CalledProcessError as e:
            click.echo(
                f"❌ Failed to clone {feature_name}@{version}: {e.stderr.strip()}"
            )
            raise SystemExit(1)
    else:
        click.echo(f"✅ Cached feature already exists: {versioned_dir}")

    # --- 3️⃣ Update pyproject.toml ------------------------------------------
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()
    pattern = rf"{feature_name}(?!@)"
    new_content = re.sub(pattern, f"{feature_name}@{version}", content)
    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    click.echo(f"🧩 Updated pyproject.toml → {feature_name}@{version}")

    # --- 4️⃣ Update symlink --------------------------------------------------
    product_features_dir = os.path.join(product_path, "features", org_safe)
    os.makedirs(product_features_dir, exist_ok=True)
    new_link = os.path.join(product_features_dir, f"{feature_name}@{version}")
    if os.path.islink(new_link):
        os.unlink(new_link)
    os.symlink(versioned_dir, new_link)
    click.echo(f"🔗 Linked {new_link} → {versioned_dir}")

    click.echo("🎯 Feature successfully attached to product.")
