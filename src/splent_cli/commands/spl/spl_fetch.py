import click

from splent_cli.services import context, spl_store


@click.command(
    "spl:fetch",
    short_help="Download an SPL model from UVLHub into the local cache.",
)
@click.argument("spl_name")
@click.option("--force", is_flag=True, help="Redownload even if already cached.")
@context.requires_detached
def spl_fetch(spl_name, force):
    """Download the UVL model for the SPL and cache it.

    The DOI comes from whatever records it: the product that pins the model,
    the working copy, or a previous fetch. The file lands under
    .splent_cache/spls/ and nowhere else, so a working copy you are editing is
    never overwritten and deleting the cache costs one download.
    """
    workspace = str(context.workspace())
    pin = spl_store.read_pin(workspace, spl_name)

    if not pin.fetchable:
        raise click.ClickException(
            spl_store.missing_model_message(workspace, spl_name, pin)
        )

    copy = spl_store.working_copy(workspace, spl_name)
    if copy is not None:
        click.secho(
            f"  Note: you are editing '{spl_name}' at {copy}, and that copy wins "
            "when the model is read.",
            fg="yellow",
        )

    path = spl_store.fetch_uvl(workspace, pin, force=force)
    click.secho(f"  Cached {spl_name} at {path}", fg="green")


cli_command = spl_fetch
