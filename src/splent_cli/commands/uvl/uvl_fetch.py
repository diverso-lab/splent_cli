import os

import click
import requests

from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    get_uvl_cfg as _get_uvl_cfg,
    resolve_uvlhub_raw_url as _resolve_uvlhub_raw_url,
)
from splent_cli.services import context


@click.command(
    "uvl:fetch",
    short_help="Download the UVL model defined in pyproject.toml",
)
@click.option("--force", is_flag=True, help="Redownload even if cached")
def fetch_uvl(force):
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    uvl_cfg = _get_uvl_cfg(data)
    mirror = uvl_cfg["mirror"]
    doi = uvl_cfg["doi"]
    file = uvl_cfg["file"]

    url = _resolve_uvlhub_raw_url(mirror, doi, file)

    uvl_dir = os.path.join(product_path, "uvl")
    os.makedirs(uvl_dir, exist_ok=True)

    target = os.path.join(uvl_dir, file)

    if os.path.exists(target) and not force:
        if not click.confirm(f"UVL file already exists: {target}\nOverwrite?"):
            click.echo("Aborted.")
            return

    click.echo(f"Downloading UVL from {url}")

    r = requests.get(url, timeout=20)
    if r.status_code != 200:
        click.echo(f"Error downloading UVL ({r.status_code})", err=True)
        raise SystemExit(1)

    with open(target, "w", encoding="utf-8") as f:
        f.write(r.text)

    click.echo(f"UVL saved to {target}")
