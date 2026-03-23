import os
import tomllib
import click
from pathlib import Path
from splent_cli.services import context


def _product_info(product_dir: Path) -> dict | None:
    pyproject = product_dir / "pyproject.toml"
    if not pyproject.exists():
        return None
    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        name = data.get("project", {}).get("name", product_dir.name)
        version = data.get("project", {}).get("version", "?")
        n_features = len(
            data.get("project", {}).get("optional-dependencies", {}).get("features", [])
        )
        return {"name": name, "version": version, "features": n_features}
    except Exception:
        return None


@click.command("product:list", short_help="List all products in the workspace.")
def product_list():
    """Lists all products found in the workspace, with their version and feature count."""
    workspace = context.workspace()
    active = os.getenv("SPLENT_APP")

    if not workspace.exists():
        click.secho(f"❌ Workspace not found: {workspace}", fg="red")
        raise SystemExit(1)

    products = []
    for d in sorted(workspace.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        info = _product_info(d)
        if info:
            products.append((d.name, info))

    if not products:
        click.secho("ℹ️  No products found in workspace.", fg="yellow")
        return

    click.secho(f"\nProducts in {workspace} ({len(products)}):\n", fg="cyan")
    for dirname, info in products:
        is_active = dirname == active
        marker = click.style(" ◀ active", fg="green", bold=True) if is_active else ""
        name_str = click.style(info["name"], bold=True)
        click.echo(
            f"  {name_str}  v{info['version']}  "
            f"({info['features']} feature{'s' if info['features'] != 1 else ''}){marker}"
        )
    click.echo()


cli_command = product_list
