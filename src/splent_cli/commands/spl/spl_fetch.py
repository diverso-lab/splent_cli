import os

import click

from splent_cli.commands.spl.spl_utils import _resolve_spl_metadata, _fetch_uvl
from splent_cli.services import context


@click.command(
    "spl:fetch",
    short_help="Download the UVL model from UVLHub into the SPL catalog",
)
@click.argument("spl_name")
@click.option("--force", is_flag=True, help="Redownload even if cached")
@context.requires_detached
def spl_fetch(spl_name, force):
    """Download the UVL model for the SPL from its configured UVLHub mirror.

    Reads mirror/DOI from splent_catalog/{spl}/metadata.toml and writes
    the downloaded UVL to splent_catalog/{spl}/{spl}.uvl.
    """
    workspace = str(context.workspace())

    metadata = _resolve_spl_metadata(spl_name)
    target = os.path.join(workspace, "splent_catalog", spl_name, f"{spl_name}.uvl")

    if os.path.exists(target) and not force:
        if not click.confirm(f"UVL file already exists: {target}\nOverwrite?"):
            click.echo("Aborted.")
            return

    _fetch_uvl(spl_name, metadata, target)
    click.secho("Done.", fg="green")


cli_command = spl_fetch
