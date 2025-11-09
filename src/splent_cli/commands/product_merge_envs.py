import os
import tomllib
import click
import shutil


def _get_product_path(product, workspace="/workspace"):
    return os.path.join(workspace, product)


def _feature_cache_dir(workspace: str, feature: str) -> str:
    """Return the base cache path for a feature (ignores version)."""
    org_safe = "splent_io"  # can be dynamic later
    feature_name = feature.split("@")[0]
    return os.path.join(workspace, ".splent_cache", "features", org_safe, feature_name)


@click.command("product:merge-envs")
@click.option("--dev", "env_name", flag_value="dev", help="Merge development envs (.env.dev.example).")
@click.option("--prod", "env_name", flag_value="prod", help="Merge production envs (.env.prod.example).")
def product_merge_envs(env_name):
    """Merge feature environment files (.env.dev/prod.example) into the product .env."""
    if not env_name:
        click.echo("❌ You must specify either --dev or --prod.")
        raise SystemExit(1)

    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not defined.")
        raise SystemExit(1)

    click.echo(f"🌍 Merging environment: {env_name}")

    product_path = _get_product_path(product, workspace)
    docker_dir = os.path.join(product_path, "docker")

    # 1️⃣ Locate product base env file
    candidates = [
        os.path.join(docker_dir, f".env.{env_name}.example"),
        os.path.join(docker_dir, ".env.example"),
        os.path.join(docker_dir, ".env"),
    ]
    base_env = next((c for c in candidates if os.path.exists(c)), None)

    if not base_env:
        click.echo(f"❌ No .env.{env_name}.example, .env.example or .env found in {docker_dir}")
        raise SystemExit(1)

    # Copy to .env (so product/docker/.env always exists)
    target_env = os.path.join(docker_dir, ".env")
    if base_env != target_env:
        shutil.copyfile(base_env, target_env)
        click.echo(f"📋 Product: using {os.path.basename(base_env)} → .env")

    # 2️⃣ Load declared features
    py_path = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(py_path):
        click.echo(f"❌ pyproject.toml not found at {py_path}")
        raise SystemExit(1)

    with open(py_path, "rb") as f:
        data = tomllib.load(f)

    features = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not features:
        click.echo("ℹ️ No features declared.")
        raise SystemExit(0)

    # 3️⃣ Prepare feature envs
    feature_env_paths = []
    for feature in features:
        feature_base = _feature_cache_dir(workspace, feature)
        docker_dir_f = os.path.join(feature_base, "docker")

        candidates_f = [
            os.path.join(docker_dir_f, f".env.{env_name}.example"),
            os.path.join(docker_dir_f, ".env.example"),
            os.path.join(docker_dir_f, ".env"),
        ]
        base_f = next((c for c in candidates_f if os.path.exists(c)), None)

        if not base_f:
            click.echo(f"⚠️ {feature}: no .env.{env_name}.example, .env.example or .env found.")
            continue

        target_f = os.path.join(docker_dir_f, ".env")
        if base_f != target_f:
            shutil.copyfile(base_f, target_f)
            click.echo(f"📋 {feature}: using {os.path.basename(base_f)} → .env")

        feature_env_paths.append(target_f)

    # 4️⃣ Merge into product .env
    merged = {}

    # Load product base env first
    with open(target_env, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                merged[k] = v

    # Merge feature envs (only add missing keys)
    for f_env in feature_env_paths:
        if not os.path.exists(f_env):
            continue
        with open(f_env, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    merged.setdefault(k, v)

    # Write merged env back
    with open(target_env, "w", encoding="utf-8") as f:
        for k, v in merged.items():
            f.write(f"{k}={v}\n")

    click.echo(f"🔗 Merged {len(feature_env_paths)} feature .env files into {target_env}")
