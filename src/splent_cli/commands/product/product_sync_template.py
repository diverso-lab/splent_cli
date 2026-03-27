import click
from pathlib import Path

from splent_cli.services import context
from splent_cli.utils.template_drift import (
    PRODUCT_GROUPS,
    count_changed_lines,
    file_diff,
    product_ctx,
    render_template,
    resolve_product_rel,
)


@click.command(
    "product:sync-template",
    short_help="Apply template updates to SPLENT-owned product files.",
)
@click.argument("product_name", required=False)
@click.option(
    "--scripts",
    "only_scripts",
    is_flag=True,
    help="Update only scripts/.",
)
@click.option(
    "--entrypoints",
    "only_entrypoints",
    is_flag=True,
    help="Update only entrypoints/.",
)
@click.option(
    "--docker",
    "only_docker",
    is_flag=True,
    help="Update only docker/ files.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would change without writing any files.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def product_sync_template(
    product_name,
    only_scripts,
    only_entrypoints,
    only_docker,
    dry_run,
    yes,
):
    """
    Update SPLENT-owned files in a product to match the current templates.
    Never touches developer-owned files (config.py, errors.py, src/, templates/).

    \b
    Examples:
        splent product:sync-template myapp
        splent product:sync-template myapp --scripts
        splent product:sync-template myapp --docker
        splent product:sync-template myapp --dry-run
        splent product:sync-template myapp --yes
    """
    workspace = context.workspace()
    if not product_name:
        product_name = context.require_app()

    product_path = workspace / product_name
    if not product_path.exists():
        click.secho(f"❌ Product '{product_name}' not found.", fg="red")
        raise SystemExit(1)

    # Select which groups to process
    filter_active = only_scripts or only_entrypoints or only_docker
    groups_to_run: dict[str, dict] = {}
    if not filter_active or only_scripts:
        groups_to_run["scripts"] = PRODUCT_GROUPS["scripts"]
    if not filter_active or only_entrypoints:
        groups_to_run["entrypoints"] = PRODUCT_GROUPS["entrypoints"]
    if not filter_active or only_docker:
        groups_to_run["docker"] = PRODUCT_GROUPS["docker"]

    ctx = product_ctx(product_name)

    # Collect all changed files
    changes: dict[str, tuple[Path, str, int]] = {}
    for files in groups_to_run.values():
        for rel_tpl, tpl_name in files.items():
            rel_path = resolve_product_rel(rel_tpl, product_name)
            abs_path = product_path / rel_path
            expected = render_template(tpl_name, ctx)
            diff = file_diff(abs_path, expected)
            if diff is not None:
                n = count_changed_lines(diff)
                changes[rel_path] = (abs_path, expected, n)

    if not changes:
        click.echo()
        click.secho("  ✅ All SPLENT-owned files are already up to date.", fg="green")
        click.echo()
        return

    click.echo()
    click.echo(click.style(f"  Files to update ({len(changes)}):\n", fg="cyan"))
    for rel_path, (_, _, n) in changes.items():
        click.echo(f"    {rel_path}  ({n} line(s) changed)")
    click.echo()

    if dry_run:
        click.secho("  (dry-run — no files written)", fg="bright_black")
        click.echo()
        return

    if not yes and not click.confirm("  Proceed with update?"):
        click.echo("  ❎ Cancelled.")
        raise SystemExit(0)

    click.echo()
    for rel_path, (abs_path, expected, _) in changes.items():
        abs_path.write_text(expected, encoding="utf-8")
        click.secho(f"  ✔  {rel_path}", fg="green")

    click.echo()
    click.secho("  Done.", fg="cyan")
    click.echo()


cli_command = product_sync_template
