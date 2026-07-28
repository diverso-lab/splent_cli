"""Shared helpers for spl:* commands.

The resolution rules themselves live in
:mod:`splent_cli.services.spl_store`. What is left here is the thin,
long-standing surface that the spl:* commands and a handful of product
commands call.
"""

import click

from splent_cli.services import context, spl_store


def _resolve_spl_metadata(spl_name: str) -> dict:
    """Everything known about an SPL, in the shape metadata.toml used to have.

    Kept in the old shape because callers pass it straight to
    :func:`_fetch_uvl`. The values no longer come from a catalog checkout:
    they are merged from the product that pins the model, the working copy,
    and the cache, in that order.
    """
    workspace = str(context.workspace())
    pin = spl_store.read_pin(workspace, spl_name)
    return {
        "spl": {
            "name": pin.name,
            "description": pin.description,
            "uvl": {
                "mirror": pin.mirror,
                "doi": pin.doi or "",
                "concept_doi": pin.concept_doi or "",
                "version": pin.version or "",
                "file": pin.remote_file,
            },
        }
    }


def _pin_from_metadata(spl_name: str, metadata: dict) -> spl_store.SplPin:
    return spl_store.pin_from_metadata(spl_name, metadata)


def _fetch_uvl(spl_name: str, metadata: dict, target: str) -> None:
    """Download an SPL model from UVLHub and write it to *target*.

    Raises ClickException when the metadata cannot say what to download or
    when UVLHub does not answer with the file.
    """
    from splent_cli.commands.uvl.uvl_utils import resolve_uvlhub_raw_url
    import os

    import requests

    from splent_cli.utils.io_utils import atomic_write

    pin = spl_store.pin_from_metadata(spl_name, metadata)
    if not pin.doi or not pin.mirror or not pin.file:
        raise click.ClickException(
            f"Incomplete UVL pointer for '{spl_name}'. Need mirror, doi, and file."
        )

    url = resolve_uvlhub_raw_url(pin.mirror, pin.doi, pin.file)
    click.echo(f"  Downloading UVL from {url}")

    try:
        response = requests.get(url, timeout=20)
    except requests.RequestException as exc:
        raise click.ClickException(f"Failed to download UVL: {exc}")

    if response.status_code != 200:
        raise click.ClickException(f"UVLHub returned {response.status_code} for {url}")

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    atomic_write(target, response.text)

    click.echo(f"  UVL saved to {target}")


def _ensure_uvl(spl_name: str) -> str:
    """Path to the SPL's UVL, downloading it into the cache when missing.

    Working copy, then cache, then UVLHub by DOI. This is what every command
    that needs to read a model goes through.
    """
    workspace = str(context.workspace())
    return spl_store.resolve_uvl(workspace, spl_name)


def _resolve_spl(spl_name: str) -> tuple[str, str]:
    """Resolve SPL name and UVL path, downloading the UVL if missing."""
    return spl_name, _ensure_uvl(spl_name)
