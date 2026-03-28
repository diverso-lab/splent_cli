import os

import click
import requests

from splent_cli.commands.spl.spl_utils import _resolve_spl_metadata
from splent_cli.commands.uvl.uvl_utils import (
    resolve_uvlhub_raw_url as _resolve_uvlhub_raw_url,
)
from splent_cli.services import context


@click.command(
    "spl:fetch",
    short_help="Download the UVL model from UVLHub into the SPL catalog",
)
@click.argument("spl_name", required=False, default=None)
@click.option("--force", is_flag=True, help="Redownload even if cached")
def spl_fetch(spl_name, force):
    """Download the UVL model for the SPL from its configured UVLHub mirror.

    \b
    If SPL_NAME is given, uses it directly.
    Otherwise reads [tool.splent].spl from the active product.

    Reads mirror/DOI from splent_catalog/{spl}/metadata.toml and writes
    the downloaded UVL to splent_catalog/{spl}/{spl}.uvl.
    """
    workspace = str(context.workspace())

    # Resolve SPL name (don't require UVL to exist yet — we're downloading it)
    if spl_name:
        name = spl_name
    else:
        product = context.active_app()
        if not product:
            raise click.ClickException(
                "No SPL specified. Pass a name or select a product:\n"
                "  splent spl:fetch <spl_name>\n"
                "  splent product:select <product>"
            )
        import tomllib

        pyproject = os.path.join(workspace, product, "pyproject.toml")
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        name = data.get("tool", {}).get("splent", {}).get("spl")
        if not name:
            raise click.ClickException(
                f"Product '{product}' has no [tool.splent].spl configured."
            )

    metadata = _resolve_spl_metadata(name)
    uvl_cfg = metadata.get("spl", {}).get("uvl", {})
    mirror = uvl_cfg.get("mirror")
    doi = uvl_cfg.get("doi")
    file = uvl_cfg.get("file")

    if not mirror or not doi or not file:
        raise click.ClickException(
            f"Incomplete [spl.uvl] in metadata.toml for '{name}'. "
            f"Need mirror, doi, and file."
        )

    url = _resolve_uvlhub_raw_url(mirror, doi, file)

    uvl_dir = os.path.join(workspace, "splent_catalog", name)
    target = os.path.join(uvl_dir, f"{name}.uvl")

    os.makedirs(uvl_dir, exist_ok=True)

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


cli_command = spl_fetch
