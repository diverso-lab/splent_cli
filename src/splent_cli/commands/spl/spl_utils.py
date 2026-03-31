"""
Shared helpers for spl:* commands.
"""

import os

import click
import requests
import tomllib

from splent_cli.services import context


def _resolve_spl_metadata(spl_name: str) -> dict:
    """Load metadata.toml for the given SPL."""
    workspace = str(context.workspace())
    metadata_path = os.path.join(workspace, "splent_catalog", spl_name, "metadata.toml")
    if not os.path.isfile(metadata_path):
        raise click.ClickException(f"Metadata not found: {metadata_path}")
    with open(metadata_path, "rb") as f:
        return tomllib.load(f)


def _fetch_uvl(spl_name: str, metadata: dict, target: str) -> None:
    """Download the UVL file from UVLHub into the catalog.

    Reads mirror/DOI/file from metadata and writes to *target*.
    Raises ClickException on failure.
    """
    from splent_cli.commands.uvl.uvl_utils import resolve_uvlhub_raw_url

    uvl_cfg = metadata.get("spl", {}).get("uvl", {})
    mirror = uvl_cfg.get("mirror")
    doi = uvl_cfg.get("doi")
    file = uvl_cfg.get("file")

    if not mirror or not doi or not file:
        raise click.ClickException(
            f"Incomplete [spl.uvl] in metadata.toml for '{spl_name}'. "
            f"Need mirror, doi, and file."
        )

    url = resolve_uvlhub_raw_url(mirror, doi, file)
    click.echo(f"  Downloading UVL from {url}")

    try:
        r = requests.get(url, timeout=20)
    except requests.RequestException as e:
        raise click.ClickException(f"Failed to download UVL: {e}")

    if r.status_code != 200:
        raise click.ClickException(f"UVLHub returned {r.status_code} for {url}")

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(r.text)

    click.echo(f"  UVL saved to {target}")


def _ensure_uvl(spl_name: str) -> str:
    """Return the path to the SPL's UVL file, downloading it if missing.

    This is the central function for on-demand UVL resolution.
    """
    workspace = str(context.workspace())
    uvl_path = os.path.join(workspace, "splent_catalog", spl_name, f"{spl_name}.uvl")

    if os.path.isfile(uvl_path):
        return uvl_path

    # UVL not on disk — download from UVLHub
    click.secho(
        f"  UVL not found locally for '{spl_name}' — fetching from UVLHub...",
        fg="yellow",
    )
    metadata = _resolve_spl_metadata(spl_name)
    _fetch_uvl(spl_name, metadata, uvl_path)
    return uvl_path


def _resolve_spl(spl_name: str) -> tuple[str, str]:
    """Resolve SPL name and UVL path, downloading the UVL if missing.

    All spl:* commands use this to get the UVL file.
    Returns (spl_name, uvl_path).
    """
    uvl_path = _ensure_uvl(spl_name)
    return spl_name, uvl_path
