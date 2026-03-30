"""
product:release — Release a product with version bump, tag, GitHub/PyPI/Docker Hub.
"""

import os
import subprocess
import click
from splent_cli.services import context, release


def _guard_all_features_pinned(product_path: str):
    """Abort if any feature is editable (not pinned to a version)."""
    from splent_cli.utils.feature_utils import load_product_features

    features = load_product_features(product_path)
    editable = [f for f in features if "@" not in f]
    if not editable:
        return

    click.secho(
        "\n  Cannot release: the following features are editable (no version pinned):\n",
        fg="red",
    )
    for f in editable:
        click.echo(f"    - {f}")
    click.echo(
        "\n  A product release must be reproducible. Pin all features first:\n"
        "    splent feature:release <feature>\n"
        "    splent feature:attach <feature> <version>\n"
    )
    raise SystemExit(1)


def _release_docker_image(product: str, version: str, docker_dir: str):
    """Build and push Docker images to Docker Hub."""
    username = os.getenv("DOCKERHUB_USERNAME")
    password = os.getenv("DOCKERHUB_PASSWORD")
    image_name = f"{username}/{product}"

    click.echo("  docker   logging into Docker Hub...")
    try:
        subprocess.run(
            ["docker", "login", "-u", username, "--password-stdin"],
            input=password, text=True, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        click.secho("  error: Docker Hub login failed", fg="red")
        raise SystemExit(1)

    click.echo("  docker   building image...")
    try:
        subprocess.run(
            [
                "docker", "build",
                "-t", f"{image_name}:{version}",
                "-t", f"{image_name}:latest",
                docker_dir,
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        click.secho("  error: Docker image build failed", fg="red")
        raise SystemExit(1)

    click.echo("  docker   pushing images...")
    try:
        subprocess.run(["docker", "push", f"{image_name}:{version}"], check=True)
        subprocess.run(["docker", "push", f"{image_name}:latest"], check=True)
    except subprocess.CalledProcessError:
        click.secho("  error: Docker image push failed", fg="red")
        raise SystemExit(1)

    click.echo("  docker   push complete")


@click.command(
    "product:release",
    short_help="Release a product: version bump, tag, GitHub/PyPI/Docker Hub.",
)
@click.argument("version", required=False, default=None)
@click.option("--product", default=None, help="Override SPLENT_APP.")
@context.requires_product
def product_release(version, product):
    product = product or os.getenv("SPLENT_APP")
    if not product:
        click.secho("  error: no product specified and SPLENT_APP not set", fg="red")
        raise SystemExit(1)

    product_path = str(context.workspace() / product)
    docker_dir = os.path.join(product_path, "docker")

    if not os.path.isfile(os.path.join(product_path, "pyproject.toml")):
        click.secho(f"  error: pyproject.toml not found in {product}", fg="red")
        raise SystemExit(1)

    _guard_all_features_pinned(product_path)

    # Determine org/repo for the semver wizard
    repo = release.get_repo_from_path(product_path)
    org, repo_name = repo.split("/")

    if not version:
        version = release.semver_wizard(org, repo_name)

    def _docker_hook(_path, ver):
        _release_docker_image(product, ver, docker_dir)

    release.run_release_pipeline(
        product,
        product_path,
        version,
        require_docker=True,
        post_pypi_hook=_docker_hook,
    )


cli_command = product_release
