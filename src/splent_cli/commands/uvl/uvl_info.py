import os
import sys
from pathlib import Path

import click
import tomllib
from splent_cli.services import context


@click.command(
    "uvl:info",
    short_help="Show UVL configuration and resolved location for the active product",
)
def uvl_info():

    workspace = str(context.workspace())
    env_path = os.path.join(workspace, ".env")

    if not os.path.exists(env_path):
        click.echo(f"Error: {workspace}/.env not found", err=True)
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
        click.echo("Error: [tool.splent.uvl] not defined", err=True)
        sys.exit(1)

    mirror = uvl_cfg.get("mirror")
    doi = uvl_cfg.get("doi")
    file = uvl_cfg.get("file")

    # -------------------------
    # Resolve URL
    # -------------------------

    if mirror == "uvlhub.io":
        url = f"https://www.uvlhub.io/doi/{doi}/files/raw/{file}/"
    else:
        url = "(unsupported mirror)"

    # -------------------------
    # Local path
    # -------------------------

    local_path = os.path.join(product_path, "uvl", file)
    exists = os.path.exists(local_path)

    # -------------------------
    # Output
    # -------------------------

    click.echo()
    click.echo(click.style("🧬 UVL Information", fg="cyan", bold=True))
    click.echo()

    click.echo(f"Product       : {app_name}")
    click.echo(f"Mirror        : {mirror}")
    click.echo(f"DOI           : {doi}")
    click.echo(f"File          : {file}")
    click.echo()

    click.echo(f"Resolved URL  : {url}")
    click.echo(f"Local path    : {local_path}")

    if exists:
        click.echo(click.style("Status        : downloaded", fg="green"))
    else:
        click.echo(click.style("Status        : not downloaded", fg="yellow"))

    click.echo()