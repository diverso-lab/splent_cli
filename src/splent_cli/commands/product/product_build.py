import os
import glob
import tomllib
import subprocess

import yaml
import click
from splent_cli.services import context


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


def merge_compose(base, override):
    """Merge two docker-compose YAML dicts."""
    result = dict(base)

    for key, value in override.items():
        if key == "services":
            result.setdefault("services", {})
            for svc, svc_def in value.items():
                result["services"][svc] = svc_def
        elif key in ("networks", "volumes"):
            result.setdefault(key, {})
            result[key].update(value)
        else:
            result[key] = value

    return result


@click.command(
    "product:build",
    short_help="Build deployment artifacts: env, compose, and Docker image.",
)
@click.option("--no-image", is_flag=True, help="Skip Docker image build.")
def product_build(no_image):
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)
    docker_path = os.path.join(product_path, "docker")
    features_path = os.path.join(product_path, "features")
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.isdir(docker_path):
        click.echo("❌ Product has no docker/ directory.")
        raise SystemExit(1)

    click.echo(f"🏗️  Building deployment artifacts for product: {product}\n")

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

    if os.path.isdir(features_path):
        for ns_dir in sorted(glob.glob(os.path.join(features_path, "*"))):
            for feature_dir in sorted(glob.glob(os.path.join(ns_dir, "*"))):
                f_docker = os.path.join(feature_dir, "docker")
                if not os.path.isdir(f_docker):
                    continue

                feature_env_file = (
                    os.path.join(f_docker, ".env.prod.example")
                    if os.path.isfile(os.path.join(f_docker, ".env.prod.example"))
                    else os.path.join(f_docker, ".env.example")
                )

                feature_env = load_env_file(feature_env_file)
                env_result = merge_env_dicts(env_result, feature_env)

    env_deploy_path = os.path.join(docker_path, ".env.deploy.example")
    with open(env_deploy_path, "w", encoding="utf-8") as f:
        for k, v in env_result.items():
            f.write(f"{k}={v}\n")

    click.echo(f"✅ Created: {env_deploy_path}")

    # ---------------------------------------------------------
    # 2) docker-compose.deploy.yml (product + features merged)
    # ---------------------------------------------------------
    click.echo("\n🐳 Generating docker-compose.deploy.yml...")

    product_compose_file = (
        os.path.join(docker_path, "docker-compose.prod.yml")
        if os.path.isfile(os.path.join(docker_path, "docker-compose.prod.yml"))
        else os.path.join(docker_path, "docker-compose.yml")
    )

    compose_result = load_compose_file(product_compose_file)

    if os.path.isdir(features_path):
        for ns_dir in sorted(glob.glob(os.path.join(features_path, "*"))):
            for feature_dir in sorted(glob.glob(os.path.join(ns_dir, "*"))):
                f_docker = os.path.join(feature_dir, "docker")
                if not os.path.isdir(f_docker):
                    continue

                feature_compose_file = (
                    os.path.join(f_docker, "docker-compose.prod.yml")
                    if os.path.isfile(os.path.join(f_docker, "docker-compose.prod.yml"))
                    else os.path.join(f_docker, "docker-compose.yml")
                )

                feature_compose = load_compose_file(feature_compose_file)
                compose_result = merge_compose(compose_result, feature_compose)

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
