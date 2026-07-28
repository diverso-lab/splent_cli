import click

from splent_cli.services import context, spl_store


def _where(workspace: str, name: str, pin: spl_store.SplPin) -> str:
    """One word for where this model would be read from right now."""
    copy = spl_store.working_copy(workspace, name)
    if copy is not None and (copy / spl_store.uvl_filename(name)).is_file():
        return "editing"
    if spl_store.find_uvl(workspace, name, version=pin.version, doi=pin.doi):
        return "cached"
    if pin.fetchable:
        return "not fetched"
    return "no model"


@click.command("spl:list", short_help="List the SPL models this workspace knows.")
@context.requires_detached
def spl_list():
    """List every SPL the workspace can name.

    That means the models you are editing (splent_spl_*/), the ones already in
    the cache, and the ones your products pin by DOI even if nothing has been
    downloaded yet.
    """
    workspace = str(context.workspace())
    names = spl_store.known_spls(workspace)

    if not names:
        click.secho("  No SPL models known to this workspace.", fg="yellow")
        click.echo("  Start one with: splent spl:create <name>")
        return

    rows = []
    for name in names:
        pin = spl_store.read_pin(workspace, name)
        rows.append((name, _where(workspace, name, pin), pin))

    width = max(len(name) for name, _, _ in rows)

    click.echo()
    click.secho(f"  SPL models ({len(rows)})", bold=True)
    click.echo()
    for name, where, pin in rows:
        label = click.style(f"{name:<{width}}", bold=True)
        state = click.style(f"  {where}", dim=True)
        version = f"  {pin.version}" if pin.version else ""
        click.echo(f"  {label}{state}{version}")
        if pin.description:
            click.secho(f"  {' ' * width}  {pin.description}", dim=True)
    click.echo()

    # A workspace that predates the pin has a splent_catalog holding every DOI
    # and no product carrying one, so every row above reads "no model". Naming
    # the fix here is the whole point: this is the command someone runs to find
    # out what is wrong, and the hint used to live only behind a hard failure.
    pending = spl_store.catalog_migration_pending(workspace)
    if pending:
        click.secho(
            f"  {len(pending)} of these are still described only by splent_catalog.",
            fg="yellow",
        )
        click.echo(f"  {spl_store.MIGRATE_HINT}")
        click.echo()


cli_command = spl_list
