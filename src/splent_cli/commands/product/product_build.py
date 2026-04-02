import os
import tomllib
import subprocess

import yaml
import click
from splent_cli.services import context, compose
from splent_cli.services.preflight import run_preflight
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.commands.product.product_env import _is_port_var


def load_env_file(path):
    """Returns dict of key=value pairs from .env.example-like file."""
    if not os.path.isfile(path):
        return {}

    env = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()

    return env


def merge_env_dicts(base, override):
    """Merge two env dicts. 'override' keys win."""
    result = dict(base)
    result.update(override)
    return result


def load_compose_file(path):
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def merge_compose(base, override, label="", build_services=None):
    """Merge two docker-compose YAML dicts. Reports conflicts via warnings.

    For services with custom builds, rewrites ``build:`` to point to the
    copied Dockerfile in the product's docker/features/ directory.
    """
    if build_services is None:
        build_services = set()
    result = dict(base)

    for key, value in override.items():
        if key == "services":
            result.setdefault("services", {})
            for svc, svc_def in value.items():
                if svc in result["services"]:
                    click.secho(
                        f"  ⚠️  Service '{svc}' overridden by: {label}",
                        fg="yellow",
                    )
                if "build" in svc_def:
                    svc_def = dict(svc_def)
                    if svc in build_services:
                        # Rewrite build context to product-local path
                        svc_def["build"] = {
                            "context": ".",
                            "dockerfile": f"features/{svc}/Dockerfile",
                        }
                        if "image" not in svc_def:
                            svc_def["image"] = f"splent/{svc}:latest"
                    else:
                        # No Dockerfile found — fall back to image only
                        if "image" not in svc_def:
                            svc_def["image"] = f"splent/{svc}:latest"
                        del svc_def["build"]
                result["services"][svc] = svc_def
        elif key in ("networks", "volumes"):
            result.setdefault(key, {})
            result[key].update(value)
        else:
            result[key] = value

    return result


def _collect_feature_dockerfiles(workspace, declared_features, docker_path, env="prod"):
    """Copy feature Dockerfiles into the product's docker/features/ directory.

    Returns set of service names that have custom builds.
    """
    import shutil

    features_build_dir = os.path.join(docker_path, "features")
    build_services = set()

    for feat in declared_features:
        clean = compose.normalize_feature_ref(feat)
        f_docker = compose.feature_docker_dir(workspace, clean)
        if not os.path.isdir(f_docker):
            continue

        compose_file = (
            os.path.join(f_docker, f"docker-compose.{env}.yml")
            if os.path.isfile(os.path.join(f_docker, f"docker-compose.{env}.yml"))
            else os.path.join(f_docker, "docker-compose.yml")
        )

        if not os.path.isfile(compose_file):
            continue

        with open(compose_file) as f:
            data = yaml.safe_load(f) or {}

        for svc_name, svc_def in data.get("services", {}).items():
            build_cfg = svc_def.get("build")
            if not build_cfg:
                continue

            # Resolve source Dockerfile path
            if isinstance(build_cfg, str):
                build_context = os.path.normpath(os.path.join(f_docker, build_cfg))
                dockerfile = "Dockerfile"
            else:
                ctx = build_cfg.get("context", ".")
                build_context = os.path.normpath(os.path.join(f_docker, ctx))
                dockerfile = build_cfg.get("dockerfile", "Dockerfile")

            src_df = os.path.join(build_context, dockerfile)
            if not os.path.isfile(src_df):
                click.secho(
                    f"  ⚠️  Dockerfile not found for {svc_name}: {src_df}",
                    fg="yellow",
                )
                continue

            # Copy to product docker/features/<svc_name>/Dockerfile
            dest_dir = os.path.join(features_build_dir, svc_name)
            os.makedirs(dest_dir, exist_ok=True)
            dest_df = os.path.join(dest_dir, "Dockerfile")
            shutil.copy2(src_df, dest_df)

            # Also copy any other files in the build context that the Dockerfile might need
            # (e.g., config files, scripts) — copy the whole docker/ dir of the feature
            for item in os.listdir(f_docker):
                src_item = os.path.join(f_docker, item)
                if item.startswith("docker-compose") or item.startswith(".env"):
                    continue  # Skip compose files and env files
                dest_item = os.path.join(dest_dir, item)
                if os.path.isfile(src_item) and not os.path.exists(dest_item):
                    shutil.copy2(src_item, dest_item)
                elif os.path.isdir(src_item) and not os.path.exists(dest_item):
                    shutil.copytree(src_item, dest_item)

            build_services.add(svc_name)

            short = svc_name.replace("splent_feature_", "")
            click.echo(
                click.style("  copy  ", dim=True)
                + f"{short}/Dockerfile"
                + click.style(f" → docker/features/{svc_name}/", dim=True)
            )

    return build_services


@click.command(
    "product:build",
    short_help="Build deployment artifacts: env, compose, and Docker image.",
)
@click.option("--no-image", is_flag=True, help="Skip Docker image build.")
@click.option(
    "--skip-preflight",
    is_flag=True,
    hidden=True,
    help="Skip pre-flight checks (used internally by product:derive).",
)
def product_build(no_image, skip_preflight):
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)
    docker_path = os.path.join(product_path, "docker")
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.isdir(docker_path):
        click.echo("❌ Product has no docker/ directory.")
        raise SystemExit(1)

    click.echo(f"🏗️  Building deployment artifacts for product: {product}\n")

    # ---------------------------------------------------------
    # Pre-flight checks (UVL + feature contracts)
    # ---------------------------------------------------------
    if not skip_preflight:
        if not run_preflight(interactive=True):
            raise SystemExit(1)

    # ---------------------------------------------------------
    # 1) .env.deploy.example (product + features merged)
    # ---------------------------------------------------------
    click.echo("📦 Generating .env.deploy.example...")

    product_env_file = (
        os.path.join(docker_path, ".env.prod.example")
        if os.path.isfile(os.path.join(docker_path, ".env.prod.example"))
        else os.path.join(docker_path, ".env.example")
    )

    env_result = load_env_file(product_env_file)

    # Read declared features from pyproject.toml (not glob)
    with open(pyproject_path, "rb") as f:
        pydata = tomllib.load(f)
    declared_features = read_features_from_data(pydata, "prod")

    # Check for editable (non-versioned) features — these can't be installed from PyPI
    editable = [f for f in declared_features if "@" not in f.split("/")[-1]]
    if editable:
        names = ", ".join(f.split("/")[-1] if "/" in f else f for f in editable)
        click.secho(
            f"  The following features are editable (not versioned):\n"
            f"    {names}\n\n"
            f"  Production builds install features from PyPI. Editable features\n"
            f"  have no published version and will fail during the Docker build.\n"
            f"  Release them first (splent feature:release) or remove them\n"
            f"  from the product (splent feature:remove).",
            fg="yellow",
        )
        if not click.confirm("\n  Continue anyway?", default=False):
            raise SystemExit(1)
        click.echo()

    seen_features: set[str] = set()
    for feat in declared_features:
        clean = compose.normalize_feature_ref(feat)
        if clean in seen_features:
            continue
        seen_features.add(clean)

        f_docker = compose.feature_docker_dir(workspace, clean)
        if not os.path.isdir(f_docker):
            continue

        feature_env_file = (
            os.path.join(f_docker, ".env.prod.example")
            if os.path.isfile(os.path.join(f_docker, ".env.prod.example"))
            else os.path.join(f_docker, ".env.example")
        )

        feature_env = load_env_file(feature_env_file)
        env_result = merge_env_dicts(env_result, feature_env)

    # Apply product port offset to feature port variables
    import zlib

    port_offset = zlib.crc32(product.encode("utf-8")) % 1000
    for k, v in env_result.items():
        if _is_port_var(k, v):
            try:
                env_result[k] = str(int(v) + port_offset)
            except ValueError:
                pass

    env_deploy_path = os.path.join(docker_path, ".env.deploy.example")
    with open(env_deploy_path, "w", encoding="utf-8") as f:
        for k, v in env_result.items():
            f.write(f"{k}={v}\n")

    click.echo(f"✅ Created: {env_deploy_path}")

    # ---------------------------------------------------------
    # 2) Copy feature Dockerfiles into product docker/features/
    # ---------------------------------------------------------
    build_services = _collect_feature_dockerfiles(
        workspace, declared_features, docker_path, "prod"
    )
    if build_services:
        click.echo(
            f"✅ Copied Dockerfiles for {len(build_services)} feature service(s)."
        )

    # ---------------------------------------------------------
    # 3) docker-compose.deploy.yml (product + features merged)
    # ---------------------------------------------------------
    click.echo("\n🐳 Generating docker-compose.deploy.yml...")

    product_compose_file = (
        os.path.join(docker_path, "docker-compose.prod.yml")
        if os.path.isfile(os.path.join(docker_path, "docker-compose.prod.yml"))
        else os.path.join(docker_path, "docker-compose.yml")
    )

    compose_result = load_compose_file(product_compose_file)

    seen_compose: set[str] = set()
    for feat in declared_features:
        clean = compose.normalize_feature_ref(feat)
        if clean in seen_compose:
            continue
        seen_compose.add(clean)

        f_docker = compose.feature_docker_dir(workspace, clean)
        if not os.path.isdir(f_docker):
            continue

        feature_compose_file = (
            os.path.join(f_docker, "docker-compose.prod.yml")
            if os.path.isfile(os.path.join(f_docker, "docker-compose.prod.yml"))
            else os.path.join(f_docker, "docker-compose.yml")
        )

        feature_compose = load_compose_file(feature_compose_file)
        label = clean.split("/")[-1] if "/" in clean else clean
        compose_result = merge_compose(
            compose_result, feature_compose, label=label, build_services=build_services
        )

    # Check for port conflicts in merged result
    port_map: dict[str, list[str]] = {}  # "host_port" -> [service_names]
    for svc_name, svc_def in compose_result.get("services", {}).items():
        for p in svc_def.get("ports", []):
            host_port = str(p).split(":")[0]
            port_map.setdefault(host_port, []).append(svc_name)

    for port, services in port_map.items():
        if len(services) > 1:
            click.secho(
                f"  ⚠️  Port {port} declared by multiple services: {', '.join(services)}",
                fg="yellow",
            )

    deploy_compose_path = os.path.join(docker_path, "docker-compose.deploy.yml")
    with open(deploy_compose_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(compose_result, f, sort_keys=False)

    click.echo(f"✅ Created: {deploy_compose_path}")

    # ---------------------------------------------------------
    # 3) Docker image build
    # ---------------------------------------------------------
    if no_image:
        click.echo("\n⏩ Skipping Docker image build (--no-image).")
    else:
        dockerfile = os.path.join(docker_path, f"Dockerfile.{product}.prod")
        if not os.path.isfile(dockerfile):
            click.secho(
                f"\n⚠️  Dockerfile not found: {dockerfile}\n"
                f"   Run 'splent product:sync-template' to generate it.",
                fg="yellow",
            )
        else:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            version = data.get("project", {}).get("version", "latest")

            click.echo(f"\n🐳 Building Docker image: {product}:{version}...")

            try:
                subprocess.run(
                    [
                        "docker",
                        "build",
                        "-t",
                        f"{product}:{version}",
                        "-t",
                        f"{product}:latest",
                        "-f",
                        dockerfile,
                        workspace,
                    ],
                    check=True,
                )
                click.echo(f"✅ Image built: {product}:{version}")
            except subprocess.CalledProcessError:
                click.secho("❌ Docker image build failed.", fg="red")
                raise SystemExit(1)

    click.echo("\n🎯 product:build completed successfully.")


cli_command = product_build
