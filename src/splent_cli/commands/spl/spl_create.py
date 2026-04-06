import os
import click
from splent_cli.services import context


@click.command("spl:create", short_help="Create a new SPL definition in the catalog.")
@click.argument("name")
@context.requires_detached
def spl_create(name):
    """Scaffold a new Software Product Line in splent_catalog/{name}/."""
    workspace = str(context.workspace())
    catalog_dir = os.path.join(workspace, "splent_catalog")
    spl_dir = os.path.join(catalog_dir, name)

    if os.path.exists(spl_dir):
        click.secho(f"❌ SPL '{name}' already exists at {spl_dir}", fg="red")
        raise SystemExit(1)

    os.makedirs(spl_dir, exist_ok=True)

    # metadata.toml
    metadata_path = os.path.join(spl_dir, "metadata.toml")
    metadata_content = (
        f"[spl]\n"
        f'name = "{name}"\n'
        f'description = ""\n'
        f"\n"
        f"[spl.uvl]\n"
        f'mirror = "uvlhub.io"\n'
        f'doi = ""\n'
        f'file = "{name}.uvl"\n'
    )
    with open(metadata_path, "w") as f:
        f.write(metadata_content)

    # {name}.uvl
    uvl_path = os.path.join(spl_dir, f"{name}.uvl")
    uvl_content = f"features\n    {name}\n        mandatory\n\nconstraints\n"
    with open(uvl_path, "w") as f:
        f.write(uvl_content)

    click.secho(f"✅ SPL '{name}' created at {spl_dir}", fg="green")


cli_command = spl_create
