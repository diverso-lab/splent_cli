"""spl:pin records which SPL model the active product derives from.

A product declares the NAME of its SPL, and a name is not resolvable on its
own. This is what writes the rest: the DOI that pins the exact model, the
concept DOI that identifies the line across versions, and the version, which
is there so a human reading the file knows what they are looking at.
"""

from __future__ import annotations

import os

import click

from splent_cli.services import context, spl_store


@click.command(
    "spl:pin",
    short_help="Record the SPL model DOI in the active product's pyproject.",
)
@click.argument("spl_name", required=False)
@click.option("--doi", default=None, help="Version DOI of the model to pin.")
@click.option(
    "--concept-doi",
    default=None,
    help="DOI of the line, stable across versions. Used to report drift.",
)
@click.option(
    "--version", default=None, help="Version label, for humans reading the file."
)
@click.option("--mirror", default=None, help=f"Defaults to {spl_store.DEFAULT_MIRROR}.")
@context.requires_product
def spl_pin(spl_name, doi, concept_doi, version, mirror):
    """Write [tool.splent.spl_model] into the active product.

    \b
    With no options it adopts whatever the workspace already knows about the
    model, which after spl:publish is the DOI that was just minted.

    \b
    With --doi it pins exactly what you say, which is what a deployment
    reproducing an older build needs.
    """
    workspace = str(context.workspace())
    product = context.require_app()
    pyproject = os.path.join(workspace, product, "pyproject.toml")

    if not os.path.isfile(pyproject):
        raise click.ClickException(f"Product pyproject.toml not found: {pyproject}")

    current = spl_store.read_product_pin(workspace, product)
    name = spl_name or (current.name if current else None)
    if not name:
        raise click.ClickException(
            f"'{product}' does not name an SPL. Set [tool.splent].spl first, or "
            "pass the name: splent spl:pin <spl_name>"
        )

    known = spl_store.read_pin(workspace, name)
    pin = spl_store.SplPin(
        name=name,
        doi=doi or known.doi,
        concept_doi=concept_doi or known.concept_doi,
        version=version or known.version,
        mirror=mirror or known.mirror,
        file=known.file,
    )

    if not pin.doi:
        click.secho(f"  Nothing records a DOI for '{name}'.", fg="yellow")
        click.echo(
            "  That is fine while you are authoring it locally, but a clone of "
            "this product will not be able to resolve the model."
        )
        click.echo(f"  Publish it first: splent spl:publish {name}")

    spl_store.write_product_pin(pyproject, pin)

    click.echo()
    click.secho(f"  {product} now pins {name}", fg="green")
    click.echo(f"    doi          {pin.doi or '(none)'}")
    click.echo(f"    concept doi  {pin.concept_doi or '(none)'}")
    click.echo(f"    version      {pin.version or '(none)'}")
    click.echo(f"    recorded in  {pyproject}")
    click.echo()


cli_command = spl_pin
