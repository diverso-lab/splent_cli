import click

from splent_cli.commands.uvl.uvl_utils import (
    list_all_features_from_uvl as _list_all_features_from_uvl,
    resolve_uvlhub_raw_url as _resolve_uvlhub_raw_url,
)
from splent_cli.services import context, spl_store


@click.command(
    "spl:info",
    short_help="Show where an SPL model lives and what points at it.",
)
@click.argument("spl_name")
@context.requires_detached
def spl_info(spl_name):
    """Show everything the workspace knows about one SPL model."""
    workspace = str(context.workspace())
    pin = spl_store.read_pin(workspace, spl_name)

    copy = spl_store.working_copy(workspace, spl_name)
    local = spl_store.find_uvl(workspace, spl_name, version=pin.version, doi=pin.doi)

    try:
        url = (
            _resolve_uvlhub_raw_url(pin.mirror, pin.doi, pin.remote_file)
            if pin.doi
            else ""
        )
    except click.ClickException:
        url = "(unsupported mirror)"

    n_features = None
    if local:
        try:
            n_features = len(_list_all_features_from_uvl(local)[0])
        except click.ClickException:
            n_features = None

    click.echo()
    click.echo("SPL info")
    click.echo(f"Name        : {pin.name}")
    if pin.description:
        click.echo(f"Description : {pin.description}")
    click.echo(f"Editing     : {copy if copy else '(no working copy)'}")
    click.echo(
        f"Cache       : {spl_store.cache_dir(workspace, spl_name, pin.version, pin.doi)}"
    )
    click.echo(f"Reading     : {local or '(nothing on disk)'}")
    if n_features is not None:
        click.echo(f"Features    : {n_features}")
    click.echo()

    click.echo(f"Mirror      : {pin.mirror}")
    click.echo(f"DOI         : {pin.doi or '(none recorded)'}")
    click.echo(f"Concept DOI : {pin.concept_doi or '(none recorded)'}")
    click.echo(f"Version     : {pin.version or '(none recorded)'}")
    click.echo(f"File        : {pin.remote_file}")
    if url:
        click.echo(f"URL         : {url}")
    click.echo()

    products = spl_store.products_pinning(workspace, spl_name)
    if products:
        click.echo("Pinned by")
        for product, version in products:
            click.echo(f"  {product}  {version}")
        click.echo()

    if local:
        click.echo(click.style("Status      : available offline", fg="green"))
    elif pin.fetchable:
        click.echo(
            click.style(
                f"Status      : not downloaded, run splent spl:fetch {spl_name}",
                fg="yellow",
            )
        )
    else:
        click.echo(click.style("Status      : no model and no DOI", fg="red"))
        # The DOI is very likely sitting in splent_catalog, unmigrated. Saying
        # so here saves the reader from concluding the model is simply lost.
        if spl_store.catalog_entry(workspace, spl_name) is not None:
            click.secho(
                "              splent_catalog still describes it. "
                f"{spl_store.MIGRATE_HINT}",
                fg="yellow",
            )

    click.echo()


cli_command = spl_info
