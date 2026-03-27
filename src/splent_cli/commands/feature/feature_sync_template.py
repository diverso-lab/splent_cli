import click
from pathlib import Path

from splent_cli.services import context
from splent_cli.utils.template_drift import (
    FEATURE_FILES,
    count_changed_lines,
    feature_ctx,
    file_diff,
    render_template,
    resolve_feature_rel,
)
from splent_cli.commands.feature.feature_drift import _resolve_cache_path


@click.command(
    "feature:sync-template",
    short_help="Apply template updates to SPLENT-owned feature scaffold files.",
)
@click.argument("feature_identifier")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would change without writing any files.",
)
@click.option("--yes", is_flag=True, help="Skip confirmation prompt.")
def feature_sync_template(feature_identifier, dry_run, yes):
    """
    Update SPLENT-owned scaffold files in a feature to match the current
    templates. Only touches: webpack.config.js, MANIFEST.in, .gitignore.
    Never touches developer-owned source files (routes, models, services…).

    \b
    Examples:
        splent feature:sync-template splent_feature_auth
        splent feature:sync-template splent_feature_auth --dry-run
        splent feature:sync-template splent_feature_auth --yes
    """
    workspace = context.workspace()
    result = _resolve_cache_path(feature_identifier, workspace)
    if result is None:
        click.secho(f"❌ Feature '{feature_identifier}' not found in cache.", fg="red")
        raise SystemExit(1)

    cache_path, org_safe, feature_name = result
    ctx = feature_ctx(org_safe, feature_name)

    # Collect changed files
    changes: dict[str, tuple[Path, str, int]] = {}
    for rel_tpl, tpl_name in FEATURE_FILES.items():
        rel_path = resolve_feature_rel(rel_tpl, org_safe, feature_name)
        abs_path = cache_path / rel_path
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


cli_command = feature_sync_template
