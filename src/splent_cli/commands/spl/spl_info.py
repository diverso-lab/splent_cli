import os

import click

from splent_cli.commands.spl.spl_utils import _resolve_spl_metadata
from splent_cli.commands.uvl.uvl_utils import (
    list_all_features_from_uvl as _list_all_features_from_uvl,
    resolve_uvlhub_raw_url as _resolve_uvlhub_raw_url,
)
from splent_cli.services import context


@click.command(
    "spl:info",
    short_help="Show SPL metadata (name, mirror, DOI, file, path)",
)
@click.argument("spl_name")
@context.requires_detached
def spl_info(spl_name):
    """Show metadata for the SPL."""
    workspace = str(context.workspace())

    name = spl_name

    metadata = _resolve_spl_metadata(name)
    spl_cfg = metadata.get("spl", {})
    uvl_cfg = spl_cfg.get("uvl", {})

    mirror = uvl_cfg.get("mirror")
    doi = uvl_cfg.get("doi")
    file = uvl_cfg.get("file")

    try:
        url = _resolve_uvlhub_raw_url(mirror, doi, file)
    except click.ClickException:
        url = "(unsupported mirror)"

    uvl_path = os.path.join(workspace, "splent_catalog", name, f"{name}.uvl")
    exists = os.path.isfile(uvl_path)

    n_features = len(_list_all_features_from_uvl(uvl_path)[0]) if exists else None

    click.echo()
    click.echo("SPL info")
    click.echo(f"Name     : {spl_cfg.get('name', name)}")
    click.echo(f"Path     : {os.path.join(workspace, 'splent_catalog', name)}")
    click.echo(f"UVL      : {uvl_path}")
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


cli_command = spl_info
