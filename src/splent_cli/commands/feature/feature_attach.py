import os
import tomllib
import tomli_w
import subprocess
import click
import requests
from splent_cli.services import context, compose
from splent_cli.utils.manifest import feature_key, set_feature_state


@click.command(
    "feature:attach",
    short_help="Attach a released feature version to the current product.",
)
@click.argument("feature_identifier", required=True)
@click.argument("version", required=True)
def feature_attach(feature_identifier, version):
    """
    Attach a released feature version to the current product.

    - Verifies that the GitHub tag exists.
    - Clones the feature version into the cache if missing.
    - Updates pyproject.toml referencing feature@version.
    - Creates/updates the versioned symlink in features/<namespace>/.
    """
    product = context.require_app()
    ws = context.workspace()

    # --- Parse feature identifier -------------------------------------------
    namespace, namespace_github, namespace_fs, feature_name = (
        compose.parse_feature_identifier(feature_identifier)
    )

    cache_base = str(ws / ".splent_cache" / "features" / namespace_fs)
    product_path = str(ws / product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("❌ pyproject.toml not found in product.")
        raise SystemExit(1)

    # --- 1️⃣ Verify GitHub tag ----------------------------------------------
    click.echo(f"🔍 Checking GitHub for {namespace_github}/{feature_name}@{version}...")

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "splent-cli"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"

    tag_api_urls = [
        f"https://api.github.com/repos/{namespace_github}/{feature_name}/git/refs/tags/{version}",
        f"https://api.github.com/repos/{namespace_github}/{feature_name}/releases/tags/{version}",
    ]
    html_url = (
        f"https://github.com/{namespace_github}/{feature_name}/releases/tag/{version}"
    )

    exists = False
    for url in tag_api_urls:
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                exists = True
                break
        except requests.RequestException:
            pass

    if not exists:
        try:
            resp = requests.get(html_url, headers=headers, timeout=5)
            if resp.status_code == 200:
                exists = True
        except requests.RequestException:
            pass

    if not exists:
        click.echo(f"❌ Tag {version} not found for {namespace_github}/{feature_name}.")
        raise SystemExit(1)

    click.echo("✅ GitHub tag exists.")

    # --- 2️⃣ Clone into cache if needed -------------------------------------
    versioned_dir = os.path.join(cache_base, f"{feature_name}@{version}")

    if not os.path.exists(versioned_dir):
        click.echo(f"⬇️  Cloning {namespace_github}/{feature_name}@{version}...")

        use_ssh = os.getenv("SPLENT_USE_SSH", "").lower() == "true"
        url = (
            f"git@github.com:{namespace_github}/{feature_name}.git"
            if use_ssh
            else f"https://github.com/{namespace_github}/{feature_name}.git"
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
            click.echo(f"✅ Feature cloned → {versioned_dir}")
        except subprocess.CalledProcessError as e:
            click.echo(f"❌ Failed to clone: {e.stderr.strip()}")
            raise SystemExit(1)
    else:
        click.echo(f"✅ Cache exists → {versioned_dir}")

    # --- 3️⃣ Update pyproject.toml ------------------------------------------
    full_name = f"{namespace}/{feature_name}@{version}"
    bare_name = f"{namespace}/{feature_name}"

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    project = data.setdefault("project", {})
    optional_deps = project.setdefault("optional-dependencies", {})
    features = optional_deps.setdefault("features", [])

    if full_name in features:
        click.echo(f"ℹ️  Feature '{full_name}' already present in pyproject.toml.")
    else:
        # Replace bare entry (added by uvl:sync) or old versioned entry if present
        features[:] = [
            f for f in features
            if f != bare_name and not f.startswith(f"{bare_name}@")
        ]
        features.append(full_name)
        with open(pyproject_path, "wb") as f:
            tomli_w.dump(data, f)
        click.echo(f"🧩 Updated pyproject.toml → {full_name}")

    # --- 4️⃣ Create/update symlink ------------------------------------------
    product_features_dir = os.path.join(product_path, "features", namespace_fs)
    os.makedirs(product_features_dir, exist_ok=True)

    new_link = os.path.join(product_features_dir, f"{feature_name}@{version}")
    if os.path.islink(new_link):
        os.unlink(new_link)
    os.symlink(versioned_dir, new_link)

    click.echo(f"🔗 Linked {new_link} → {versioned_dir}")

    # --- 5️⃣ Update manifest ------------------------------------------------
    key = feature_key(namespace_fs, feature_name, version)
    set_feature_state(
        product_path, product, key, "declared",
        namespace=namespace_fs, name=feature_name, version=version, mode="pinned",
    )

    click.echo("🎯 Feature successfully attached.")
