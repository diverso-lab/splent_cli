import os
import tomllib
import subprocess
import shutil
import click


@click.command(
    "product:env", help="Generate or merge .env files for the active product."
)
@click.option(
    "--generate",
    is_flag=True,
    help="Generate the .env file for the product (and optionally all features).",
)
@click.option(
    "--merge", is_flag=True, help="Merge all feature .env files into the product .env."
)
@click.option(
    "--dev",
    "env_name",
    flag_value="dev",
    help="Use development env (.env.dev.example).",
)
@click.option(
    "--prod",
    "env_name",
    flag_value="prod",
    help="Use production env (.env.prod.example).",
)
@click.option(
    "--all",
    "process_all",
    is_flag=True,
    help="Also process all features declared in pyproject.toml (only for --generate).",
)
def product_env(generate, merge, env_name, process_all):
    """
    Manage environment files for the active product.

    Examples:
        splent product:env --generate --dev
        splent product:env --generate --prod
        splent product:env --generate --all --dev
        splent product:env --merge --dev
    """
    workspace = "/workspace"
    product = os.getenv("SPLENT_APP")

    if not product:
        click.echo("❌ SPLENT_APP not defined. Please select a product first.")
        raise SystemExit(1)

    if not env_name:
        click.echo("❌ You must specify --dev or --prod.")
        raise SystemExit(1)

    product_path = os.path.join(workspace, product)
    docker_dir = os.path.join(product_path, "docker")
    py_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(py_path):
        click.echo(f"❌ pyproject.toml not found at {py_path}")
        raise SystemExit(1)

    if not os.path.exists(docker_dir):
        click.echo(f"❌ docker directory not found at {docker_dir}")
        raise SystemExit(1)

    if not (generate or merge):
        click.echo("❌ You must specify either --generate or --merge.")
        raise SystemExit(1)

    # ======================================================
    # 1️⃣ GENERATE MODE
    # ======================================================
    if generate:
        env_file = os.path.join(docker_dir, ".env")
        example_specific = os.path.join(docker_dir, f".env.{env_name}.example")
        example_generic = os.path.join(docker_dir, ".env.example")

        click.echo(f"🚀 Generating .env for product '{product}' ({env_name})...")

        if os.path.exists(env_file):
            click.echo(f"ℹ️  Existing .env found for {product}, skipping creation.")
        else:
            selected = (
                example_specific
                if os.path.exists(example_specific)
                else example_generic
            )
            if selected:
                shutil.copyfile(selected, env_file)
                click.echo(
                    f"📄 Created {product}/docker/.env from {os.path.basename(selected)}"
                )
            else:
                click.echo(f"⚠️  No .env template found for {product} in {docker_dir}")

        # If only product env is needed, stop here
        if not process_all:
            click.echo("✅ Product .env generation complete.")
            return

        # Process all declared features
        with open(py_path, "rb") as f:
            data = tomllib.load(f)

        features = (
            data.get("project", {}).get("optional-dependencies", {}).get("features", [])
        )
        if not features:
            click.echo("ℹ️ No features declared in pyproject.toml.")
            return

        click.echo(
            f"🚀 Generating .env files for {len(features)} features ({env_name})...\n"
        )

        created, skipped, no_template, failed = 0, 0, 0, 0
        for f_entry in features:
            base_name = f_entry.split("@")[0]
            cmd = ["splent", "feature:env", base_name, "--generate", f"--{env_name}"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            output = result.stdout.strip()
            lines = [l.strip() for l in output.splitlines() if l.strip()]
            cleaned = "\n".join(lines)
            if cleaned:
                click.echo(cleaned)

            if "docker directory not found" in cleaned:
                no_template += 1
                continue
            if "No .env template" in cleaned or "⚠️" in cleaned:
                no_template += 1
                continue
            if result.returncode != 0:
                failed += 1
                continue
            if "Created" in cleaned:
                created += 1
            elif "Existing" in cleaned:
                skipped += 1
            else:
                skipped += 1

        click.echo("\n✅ Feature .env generation complete.")
        click.echo(f"   📄 Created: {created}")
        click.echo(f"   ⏩ Skipped: {skipped}")
        click.echo(f"   📭 No template: {no_template}")
        click.echo(f"   💥 Failed: {failed}")
        return

    # ======================================================
    # 2️⃣ MERGE MODE
    # ======================================================
    if merge:
        click.echo(f"🌍 Merging environment: {env_name}")

        # Locate product base env file
        candidates = [
            os.path.join(docker_dir, f".env.{env_name}.example"),
            os.path.join(docker_dir, ".env.example"),
            os.path.join(docker_dir, ".env"),
        ]
        base_env = next((c for c in candidates if os.path.exists(c)), None)

        if not base_env:
            click.echo(
                f"❌ No .env.{env_name}.example, .env.example or .env found in {docker_dir}"
            )
            raise SystemExit(1)

        target_env = os.path.join(docker_dir, ".env")
        if base_env != target_env:
            shutil.copyfile(base_env, target_env)
            click.echo(f"📋 Product: using {os.path.basename(base_env)} → .env")

        with open(py_path, "rb") as f:
            data = tomllib.load(f)
        features = (
            data.get("project", {}).get("optional-dependencies", {}).get("features", [])
        )
        if not features:
            click.echo("ℹ️ No features declared.")
            raise SystemExit(0)

        merged = {}
        with open(target_env, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    merged[k] = v

        feature_env_paths = []
        for feature in features:
            org_part, feat_part = (
                feature.split("/", 1) if "/" in feature else ("splent_io", feature)
            )
            docker_dir_f = os.path.join(
                workspace, ".splent_cache", "features", org_part, feat_part, "docker"
            )

            candidates_f = [
                os.path.join(docker_dir_f, ".env"),  # preferimos la .env ya generada
                os.path.join(docker_dir_f, f".env.{env_name}.example"),
                os.path.join(docker_dir_f, ".env.example"),
            ]
            base_f = next((c for c in candidates_f if os.path.exists(c)), None)
            if not base_f:
                click.echo(
                    f"⚠️ {feature}: no .env.{env_name}.example, .env.example or .env found."
                )
                continue

            target_f = os.path.join(docker_dir_f, ".env")
            if base_f != target_f:
                shutil.copyfile(base_f, target_f)
                click.echo(f"📋 {feature}: using {os.path.basename(base_f)} → .env")

            feature_env_paths.append(target_f)

        for f_env in feature_env_paths:
            if not os.path.exists(f_env):
                continue
            with open(f_env, "r", encoding="utf-8") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        k, v = line.strip().split("=", 1)
                        merged.setdefault(k, v)

        with open(target_env, "w", encoding="utf-8") as f:
            for k, v in merged.items():
                f.write(f"{k}={v}\n")

        click.echo(
            f"🔗 Merged {len(feature_env_paths)} feature .env files into {target_env}"
        )
