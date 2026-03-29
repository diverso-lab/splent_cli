"""
Shared helpers for spl:* commands.
"""

import os

import click
import tomllib

from splent_cli.services import context


def _resolve_spl(spl_name: str) -> tuple[str, str]:
    """Resolve SPL name and UVL path.

    All spl:* commands require the SPL name as argument.
    Returns (spl_name, uvl_path).
    """
    workspace = str(context.workspace())
    uvl_path = os.path.join(workspace, "splent_catalog", spl_name, f"{spl_name}.uvl")
    if not os.path.isfile(uvl_path):
        raise click.ClickException(f"UVL not found: {uvl_path}")

    return spl_name, uvl_path


def _resolve_spl_metadata(spl_name: str) -> dict:
    """Load metadata.toml for the given SPL."""
    workspace = str(context.workspace())
    metadata_path = os.path.join(
        workspace, "splent_catalog", spl_name, "metadata.toml"
    )
    if not os.path.isfile(metadata_path):
        raise click.ClickException(f"Metadata not found: {metadata_path}")
    with open(metadata_path, "rb") as f:
        return tomllib.load(f)
