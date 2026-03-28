"""
Shared helpers for spl:* commands.
"""

import os

import click
import tomllib

from splent_cli.services import context


def _resolve_spl(spl_name_arg: str | None) -> tuple[str, str]:
    """Resolve SPL name and UVL path.

    If spl_name_arg is provided, use it directly.
    If not, read from active product's [tool.splent].spl.
    Returns (spl_name, uvl_path).
    """
    workspace = str(context.workspace())

    if spl_name_arg:
        spl_name = spl_name_arg
    else:
        product = context.active_app()
        if not product:
            raise click.ClickException(
                "No SPL specified. Pass a name or select a product:\n"
                "  splent spl:features <spl_name>\n"
                "  splent product:select <product>"
            )
        pyproject = os.path.join(workspace, product, "pyproject.toml")
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        spl_name = data.get("tool", {}).get("splent", {}).get("spl")
        if not spl_name:
            raise click.ClickException(
                f"Product '{product}' has no [tool.splent].spl configured."
            )

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
