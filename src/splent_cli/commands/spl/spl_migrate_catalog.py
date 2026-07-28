"""spl:migrate-catalog moves what splent_catalog held into the places it belongs.

The catalog was one git repository holding a .gitignore and three
metadata.toml files, because the models themselves were gitignored and lived
in UVLHub. Everything it actually did is now done by two things that already
had to exist:

  * the DOI, which each product records in its own pyproject.toml, so a
    product can resolve its model with nothing but its own repository and
    UVLHub;
  * the model file, which lives in a working copy when it is being edited and
    in .splent_cache/spls/ when it is merely being used.

This command reads the catalog one last time and writes both. It never
deletes the catalog directory. It is the developer's repository, it may hold
uncommitted work, and removing it is a decision for a human with git in hand.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from splent_cli.services import context, spl_store
from splent_cli.utils.io_utils import load_toml

CATALOG_DIRNAME = "splent_catalog"


def _catalog_entries(catalog: Path) -> list[tuple[str, spl_store.SplPin, Path | None]]:
    """(name, pin, local uvl) for every SPL the catalog describes."""
    entries = []
    for metadata_path in sorted(catalog.glob("*/metadata.toml")):
        spl_dir = metadata_path.parent
        name = spl_dir.name
        try:
            data = load_toml(metadata_path, what=f"{name}/metadata.toml")
        except click.ClickException:
            continue
        pin = spl_store.pin_from_metadata(name, data)
        local = spl_dir / spl_store.uvl_filename(name)
        if not local.is_file():
            found = sorted(p for p in spl_dir.glob("*.uvl"))
            local = found[0] if found else None
        entries.append((name, pin, local))
    return entries


@click.command(
    "spl:migrate-catalog",
    short_help="One-off: move splent_catalog into the cache and product pins.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be written without writing anything.",
)
@context.requires_detached
def spl_migrate_catalog(dry_run):
    """Adopt everything splent_catalog holds, then stop needing it.

    \b
    For each SPL in the catalog:
      1. its model file, if present, is copied into .splent_cache/spls/
         so nothing has to be downloaded to keep working offline;
      2. its DOI is written into every product that names it, under
         [tool.splent.spl_model], so those products stop needing a catalog
         to resolve anything.

    \b
    The splent_catalog directory is left exactly where it is. Delete it
    yourself once you are satisfied, and only with git.
    """
    workspace = Path(context.workspace())
    catalog = workspace / CATALOG_DIRNAME

    click.echo()
    click.secho("  spl:migrate-catalog", bold=True)
    click.echo()

    if not catalog.is_dir():
        click.secho(
            f"  No {CATALOG_DIRNAME}/ in {workspace}, nothing to migrate.", fg="yellow"
        )
        click.echo()
        return

    entries = _catalog_entries(catalog)
    if not entries:
        click.secho(f"  {CATALOG_DIRNAME}/ describes no SPLs.", fg="yellow")
        click.echo()
        return

    cached = 0
    pinned = 0
    skipped: list[str] = []

    for name, pin, local in entries:
        click.secho(f"  {name}", bold=True)

        if pin.doi:
            click.echo(f"    doi     {pin.doi}")
        else:
            click.secho(
                "    doi     none recorded, products will need a working copy",
                fg="yellow",
            )

        # 1. Seed the cache so offline work survives with no fetch at all.
        target_dir = spl_store.cache_dir(workspace, name, pin.version, pin.doi)
        target = target_dir / spl_store.uvl_filename(name)
        if local is None:
            click.secho(
                "    model   not on disk, will be fetched on demand", fg="yellow"
            )
        elif target.is_file():
            click.echo(f"    model   already cached at {target}")
        elif dry_run:
            click.echo(f"    model   would copy {local.name} to {target}")
            cached += 1
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, target)
            spl_store.write_metadata(target_dir, pin)
            click.echo(f"    model   cached at {target}")
            cached += 1

        # 2. Record the DOI in every product that names this SPL, which is the
        #    whole point: after this, deriving needs the product and UVLHub.
        for product, _ in spl_store.products_pinning(workspace, name):
            pyproject = workspace / product / "pyproject.toml"
            current = spl_store.read_product_pin(workspace, product)
            if current and current.doi == pin.doi and current.doi:
                click.echo(f"    pin     {product} already records this DOI")
                continue
            if not pin.doi:
                skipped.append(f"{product} ({name} has no DOI)")
                continue
            if dry_run:
                click.echo(f"    pin     would record the DOI in {product}")
                pinned += 1
                continue
            merged = spl_store.SplPin(
                name=name,
                doi=pin.doi,
                concept_doi=pin.concept_doi
                or (current.concept_doi if current else None),
                version=pin.version or (current.version if current else None),
                mirror=pin.mirror,
                file=pin.file,
            )
            spl_store.write_product_pin(pyproject, merged)
            click.echo(f"    pin     recorded in {product}/pyproject.toml")
            pinned += 1

        click.echo()

    verb = "would cache" if dry_run else "cached"
    verb2 = "would pin" if dry_run else "pinned"
    click.secho(f"  {verb} {cached} model(s), {verb2} {pinned} product(s).", fg="green")
    if skipped:
        click.echo()
        click.secho("  Left alone because no DOI was recorded:", fg="yellow")
        for item in skipped:
            click.echo(f"    {item}")
        click.echo(
            "  Publish the model (splent spl:publish <name>) and the DOI lands "
            "on its own."
        )
    click.echo()
    click.secho(
        f"  {CATALOG_DIRNAME}/ was not touched. Nothing in the CLI reads it any more.",
        dim=True,
    )
    click.echo()


cli_command = spl_migrate_catalog
