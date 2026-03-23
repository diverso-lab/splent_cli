import os

import click

from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    get_uvl_cfg as _get_uvl_cfg,
    resolve_uvlhub_raw_url as _resolve_uvlhub_raw_url,
    list_all_features_from_uvl as _list_all_features_from_uvl,
)
from splent_cli.services import context


@click.command(
    "uvl:info",
    short_help="Show UVL configuration and resolved location for the active product",
)
def uvl_info():
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    uvl_cfg = _get_uvl_cfg(data)
    mirror = uvl_cfg.get("mirror")
    doi = uvl_cfg.get("doi")
    file = uvl_cfg.get("file")

    try:
        url = _resolve_uvlhub_raw_url(mirror, doi, file)
    except click.ClickException:
        url = "(unsupported mirror)"

    local_path = os.path.join(product_path, "uvl", file)
    exists = os.path.exists(local_path)

    n_features = len(_list_all_features_from_uvl(local_path)[0]) if exists else None

    click.echo()
    click.echo("UVL info")
    click.echo(f"Product  : {app_name}")
    click.echo(f"UVL      : {local_path}")
    if n_features is not None:
        click.echo(f"Features : {n_features}")
    click.echo()

    click.echo(f"Mirror   : {mirror}")
    click.echo(f"DOI      : {doi}")
    click.echo(f"File     : {file}")
    click.echo(f"URL      : {url}")
    click.echo()

    if exists:
        click.echo(click.style("Status   : downloaded", fg="green"))
    else:
        click.echo(click.style("Status   : not downloaded", fg="yellow"))

    click.echo()
