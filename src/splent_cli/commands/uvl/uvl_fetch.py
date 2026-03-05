import os
import sys
from pathlib import Path

import click
import requests
import tomllib


@click.command(
    "uvl:fetch",
    short_help="Download the UVL model defined in pyproject.toml",
)
@click.option("--force", is_flag=True, help="Redownload even if cached")
def fetch_uvl(force):

    workspace = "/workspace"
    env_path = os.path.join(workspace, ".env")

    if not os.path.exists(env_path):
        click.echo("Error: /workspace/.env not found", err=True)
        sys.exit(1)

    # -------------------------
    # Read SPLENT_APP
    # -------------------------

    app_name = None
    with open(env_path) as f:
        for line in f:
            if line.startswith("SPLENT_APP="):
                app_name = line.strip().split("=", 1)[1]

    if not app_name:
        click.echo("Error: SPLENT_APP not set", err=True)
        sys.exit(1)

    product_path = os.path.join(workspace, app_name)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.echo("Error: pyproject.toml not found", err=True)
        sys.exit(1)

    # -------------------------
    # Load pyproject
    # -------------------------

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    try:
        uvl_cfg = data["tool"]["splent"]["uvl"]
    except KeyError:
        click.echo("Error: [tool.splent.uvl] not defined in pyproject", err=True)
        sys.exit(1)

    mirror = uvl_cfg["mirror"]
    doi = uvl_cfg["doi"]
    file = uvl_cfg["file"]

    # -------------------------
    # Resolve URL
    # -------------------------

    if mirror == "uvlhub.io":
        url = f"https://www.uvlhub.io/doi/{doi}/files/raw/{file}/"
    else:
        click.echo(f"Error: unsupported mirror '{mirror}'", err=True)
        sys.exit(1)

    # -------------------------
    # Target path
    # -------------------------

    uvl_dir = os.path.join(product_path, "uvl")
    os.makedirs(uvl_dir, exist_ok=True)

    target = os.path.join(uvl_dir, file)

    if os.path.exists(target) and not force:
        click.echo(f"UVL already present: {target}")
        return

    # -------------------------
    # Download
    # -------------------------

    click.echo(f"Downloading UVL from {url}")

    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        click.echo(f"Error downloading UVL ({r.status_code})", err=True)
        sys.exit(1)

    with open(target, "w", encoding="utf-8") as f:
        f.write(r.text)

    click.echo(f"UVL saved to {target}")