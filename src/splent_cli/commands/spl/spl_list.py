import os
import click

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from splent_cli.services import context


@click.command("spl:list", short_help="List all SPLs in the catalog.")
@context.requires_detached
def spl_list():
    """Lists all SPLs found in splent_catalog/ with their metadata."""
    workspace = str(context.workspace())
    catalog_dir = os.path.join(workspace, "splent_catalog")

    if not os.path.isdir(catalog_dir):
        click.secho("ℹ️  No splent_catalog/ directory found in workspace.", fg="yellow")
        return

    spls = []
    for entry in sorted(os.listdir(catalog_dir)):
        entry_path = os.path.join(catalog_dir, entry)
        if not os.path.isdir(entry_path) or entry.startswith("."):
            continue
        metadata_path = os.path.join(entry_path, "metadata.toml")
        if not os.path.isfile(metadata_path):
            continue
        try:
            with open(metadata_path, "rb") as f:
                data = tomllib.load(f)
            spl_data = data.get("spl", {})
            name = spl_data.get("name", entry)
            description = spl_data.get("description", "")
            uvl_file = spl_data.get("uvl", {}).get("file", "")
            spls.append({"name": name, "description": description, "uvl_file": uvl_file})
        except Exception:
            spls.append({"name": entry, "description": "(unreadable metadata)", "uvl_file": ""})

    if not spls:
        click.secho("ℹ️  No SPLs found in splent_catalog/.", fg="yellow")
        return

    click.secho(f"\nSPLs in catalog ({len(spls)}):\n", fg="cyan")
    for spl in spls:
        name_str = click.style(spl["name"], bold=True)
        desc = f"  — {spl['description']}" if spl["description"] else ""
        uvl = f"  [{spl['uvl_file']}]" if spl["uvl_file"] else ""
        click.echo(f"  {name_str}{desc}{uvl}")
    click.echo()


cli_command = spl_list
