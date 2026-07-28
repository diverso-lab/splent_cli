import os

import click

from splent_cli.services import context, spl_store


@click.command(
    "spl:create",
    short_help="Start a new SPL model in its own workspace directory.",
)
@click.argument("name")
@context.requires_detached
def spl_create(name):
    """Scaffold a new Software Product Line at splent_spl_<name>/.

    The model lives at the workspace root next to splent_feature_<name>/
    directories, because it is authored the same way they are: it is yours, it
    can be its own git repository, and it is the source of truth until you
    publish it.

    A brand new model has no DOI. Everything keeps working without one, and
    spl:publish fills it in the first time you push the model out.
    """
    workspace = str(context.workspace())
    spl_dir = spl_store.working_copy_candidates(workspace, name)[0]

    existing = spl_store.working_copy(workspace, name)
    if existing is not None:
        click.secho(f"  SPL '{name}' already exists at {existing}", fg="red")
        raise SystemExit(1)

    os.makedirs(spl_dir, exist_ok=True)

    spl_store.write_metadata(spl_dir, spl_store.SplPin(name=name))

    # A fresh SPL has no features yet, so emit a minimal VALID model (just the
    # root, no empty 'mandatory'/'optional' group, which UVL rejects). Tabs are
    # used for indentation, consistent with spl:add-feature.
    uvl_path = spl_dir / spl_store.uvl_filename(name)
    uvl_path.write_text(f"features\n\t{name}\n", encoding="utf-8")

    # Unlike the old catalog, the model itself is tracked here. Only the
    # backups spl:add-feature leaves behind are noise.
    (spl_dir / ".gitignore").write_text("*.uvl.bak\n", encoding="utf-8")

    click.secho(f"  SPL '{name}' created at {spl_dir}", fg="green")
    click.echo(f"  Model  {uvl_path}")
    click.echo("  Publish it with: splent spl:publish " + name)


cli_command = spl_create
