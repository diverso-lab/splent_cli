import logging
import os
import shlex
import subprocess

import click

from splent_cli.services import compose, context
from splent_cli.utils.feature_utils import (
    get_features_from_pyproject,
    get_normalize_feature_name_in_splent_format,
)

logger = logging.getLogger(__name__)


def _is_product_container() -> bool:
    """Return True when running inside the product's web container (not the CLI)."""
    return os.path.exists("/.dockerenv") and os.getenv("SPLENT_CONTAINER") != "cli"


@click.command(
    "feature:compile", help="Compile frontend assets for one or all features."
)
@click.argument("feature_name", required=False)
@click.option("--watch", is_flag=True, help="Enable watch mode for development.")
@click.option("--dev", "env_dev", is_flag=True, help="Use development environment.")
@click.option("--prod", "env_prod", is_flag=True, help="Use production environment.")
def feature_compile(feature_name, watch, env_dev, env_prod):
    production = os.getenv("FLASK_ENV", "develop") == "production" or env_prod

    features = (
        [get_normalize_feature_name_in_splent_format(feature_name)]
        if feature_name
        else get_features_from_pyproject()
    )

    env = context.resolve_env(env_dev, env_prod)
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)

    # If running inside the product's web container, webpack is available locally.
    # Otherwise (CLI container or host), delegate to the product container via docker exec.
    if _is_product_container():
        container_id = None
    else:
        docker_dir = os.path.join(product_path, "docker")
        compose_file = compose.resolve_file(product_path, env)

        if not compose_file:
            raise click.ClickException(
                f"No docker-compose file found for {product} ({env})"
            )

        project_name = compose.project_name(product, env)
        container_id = compose.find_main_container(
            project_name, compose_file, docker_dir
        )

        if not container_id:
            raise click.ClickException(
                f"No running container found for {product} ({env}) — run: splent product:up --{env}"
            )

    for feature in features:
        _compile_in_container(
            container_id, feature, watch, production, workspace, product
        )


def _find_webpack(workspace, product, org_safe, base_name, version):
    """Locate webpack.config.js across all possible feature locations."""
    rel_path = os.path.join(
        "src", org_safe, base_name, "assets", "js", "webpack.config.js"
    )

    # 1. Workspace root (editable)
    candidate = os.path.join(workspace, base_name, rel_path)
    if os.path.exists(candidate):
        return candidate

    # 2. Product symlinks (resolves to wherever the symlink points)
    if version:
        dir_name = f"{base_name}@{version}"
    else:
        dir_name = base_name
    candidate = os.path.join(
        workspace, product, "features", org_safe, dir_name, rel_path
    )
    if os.path.exists(candidate):
        return candidate

    # 3. Cache (pinned, versioned)
    if version:
        candidate = os.path.join(
            workspace,
            ".splent_cache",
            "features",
            org_safe,
            f"{base_name}@{version}",
            rel_path,
        )
        if os.path.exists(candidate):
            return candidate

    return None


def _compile_in_container(container_id, feature, watch, production, workspace, product):
    parts = feature.split("/")
    if len(parts) == 2:
        org_raw, name_version = parts
        org_safe = org_raw.replace("-", "_").replace(".", "_")
    else:
        org_safe, name_version = "splent_io", parts[0]

    base_name, _, version = name_version.partition("@")
    version = version or None

    webpack_file = _find_webpack(workspace, product, org_safe, base_name, version)

    if not webpack_file:
        click.echo(
            click.style(
                f"⚠ No webpack.config.js found in {feature}, skipping...", fg="yellow"
            )
        )
        return

    click.echo(click.style(f"🚀 Compiling {feature}...", fg="cyan"))

    mode = "production" if production else "development"
    product_root = os.path.join(workspace, product)

    cmd_parts = ["npx", "webpack", "--config", webpack_file, "--mode", mode, "--color"]
    if watch and not production:
        cmd_parts.append("--watch")
    if not production:
        cmd_parts.extend(["--devtool=source-map", "--no-cache"])

    shell_cmd = " ".join(shlex.quote(p) for p in cmd_parts)
    cd_cmd = f"cd {shlex.quote(product_root)} && {shell_cmd}"

    # Inside a container: run webpack directly.
    # From the host: run via `docker exec` so webpack uses the container's
    # node_modules.
    if container_id:
        run_cmd = ["docker", "exec", container_id, "bash", "-c", cd_cmd]
    else:
        run_cmd = ["bash", "-c", cd_cmd]

    try:
        if watch:
            subprocess.Popen(
                run_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            click.echo(
                click.style(f"👀 Watching {feature} in {mode} mode...", fg="blue")
            )
        else:
            subprocess.run(run_cmd, check=True)
            click.echo(
                click.style(
                    f"✅ Successfully compiled {feature} in {mode} mode!", fg="green"
                )
            )
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"❌ Error compiling {feature}: {e}", fg="red"))
