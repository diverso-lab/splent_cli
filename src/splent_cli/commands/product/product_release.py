import os
import subprocess
import click
from splent_cli.services import context, release


# =====================================================================
# VALIDACIONES
# =====================================================================
def validate_product_release_env():
    missing = []

    if not os.getenv("GITHUB_TOKEN"):
        click.echo("⚠️ Warning: GITHUB_TOKEN not set → GitHub release will be skipped.")

    pypi_user = os.getenv("TWINE_USERNAME") or os.getenv("PYPI_USERNAME")
    pypi_pass = os.getenv("TWINE_PASSWORD") or os.getenv("PYPI_PASSWORD")

    if not pypi_user:
        missing.append("TWINE_USERNAME or PYPI_USERNAME")
    if not pypi_pass:
        missing.append("TWINE_PASSWORD or PYPI_PASSWORD")

    if not os.getenv("DOCKERHUB_USERNAME"):
        missing.append("DOCKERHUB_USERNAME")
    if not os.getenv("DOCKERHUB_PASSWORD"):
        missing.append("DOCKERHUB_PASSWORD")

    if missing:
        click.echo("❌ Missing required environment variables:")
        for m in missing:
            click.echo(f"   - {m}")
        raise SystemExit(1)


# =====================================================================
# DOCKER RELEASE
# =====================================================================
def release_docker_image(product, version, docker_dir):
    username = os.getenv("DOCKERHUB_USERNAME")
    password = os.getenv("DOCKERHUB_PASSWORD")

    click.echo("🐳 Logging into Docker Hub...")
    try:
        subprocess.run(
            ["docker", "login", "-u", username, "--password-stdin"],
            input=password,
            text=True,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        click.secho(
            "❌ Docker Hub login failed."
            " Check DOCKERHUB_USERNAME and DOCKERHUB_PASSWORD.",
            fg="red",
        )
        raise SystemExit(1)

    image_name = f"{username}/{product}"

    click.echo("🐳 Building Docker image...")
    try:
        subprocess.run(
            [
                "docker",
                "build",
                "-t",
                f"{image_name}:{version}",
                "-t",
                f"{image_name}:latest",
                docker_dir,
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        click.secho("❌ Docker image build failed.", fg="red")
        raise SystemExit(1)

    click.echo("📤 Pushing Docker images...")
    try:
        subprocess.run(
            ["docker", "push", f"{image_name}:{version}"], check=True
        )
        subprocess.run(
            ["docker", "push", f"{image_name}:latest"], check=True
        )
    except subprocess.CalledProcessError:
        click.secho(
            "❌ Docker image push failed."
            " Check network and DockerHub permissions.",
            fg="red",
        )
        raise SystemExit(1)

    click.echo("✅ Docker Hub release complete.")


# =====================================================================
# COMMAND
# =====================================================================
@click.command(
    "product:release",
    short_help="Release a product: version bump, tag, GitHub release, PyPI release, DockerHub release.",
)
@click.argument("version")
@click.option("--product", default=None, help="Override SPLENT_APP.")
@context.requires_product
def product_release(version, product):
    validate_product_release_env()

    product = product or os.getenv("SPLENT_APP")

    if not product:
        click.echo("❌ No product specified and SPLENT_APP not set.")
        raise SystemExit(1)

    product_path = str(context.workspace() / product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")
    docker_dir = os.path.join(product_path, "docker")

    if not os.path.isfile(pyproject_path):
        click.echo(f"❌ pyproject.toml not found in product: {product}")
        raise SystemExit(1)

    click.echo(f"🚀 Releasing PRODUCT {product}@{version}")

    release.update_version(pyproject_path, version)
    release.commit_local_changes(product_path, version, subject="bump product version")

    release.create_and_push_git_tag(product_path, version)
    remote_url = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        cwd=product_path,
    ).stdout.strip()
    repo = release.extract_repo(remote_url)

    release.create_github_release(repo, version, os.getenv("GITHUB_TOKEN"))
    release.build_and_upload_pypi(product_path)
    release_docker_image(product, version, docker_dir)

    click.echo("🎉 Product release completed!")
