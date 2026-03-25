import click
from pathlib import Path

from splent_cli.services import context
from splent_cli.utils.template_drift import (
    CLI_VERSION,
    PRODUCT_GROUPS,
    GROUP_LABELS,
    count_changed_lines,
    file_diff,
    get_stored_cli_version,
    product_ctx,
    render_template,
    resolve_product_rel,
)


def _print_diff_lines(diff: list[str]) -> None:
    for line in diff:
        line = line.rstrip()
        if line.startswith("+++") or line.startswith("---"):
            click.secho("        " + line, fg="bright_black")
        elif line.startswith("+"):
            click.secho("        " + line, fg="green")
        elif line.startswith("-"):
            click.secho("        " + line, fg="red")
        elif line.startswith("@@"):
            click.secho("        " + line, fg="cyan")
        else:
            click.echo("        " + line)


def _check_group(
    product_path: Path,
    group_name: str,
    files: dict,
    ctx: dict,
    product_name: str,
    verbose: bool,
) -> bool:
    """Print drift status for one group. Returns True if any file changed."""
    label = GROUP_LABELS[group_name]
    rows = []
    for rel_tpl, tpl_name in files.items():
        rel_path = resolve_product_rel(rel_tpl, product_name)
        abs_path = product_path / rel_path
        expected = render_template(tpl_name, ctx)
        diff = file_diff(abs_path, expected)
        rows.append((rel_path, diff))

    click.echo()
    click.echo(
        click.style(f"  {label}", bold=True)
        + click.style("  [SPLENT-owned — safe to update]", fg="bright_black")
    )
    any_changed = False
    for rel_path, diff in rows:
        filename = Path(rel_path).name
        if diff is None:
            status = click.style("✔  up to date", fg="green")
        else:
            n = count_changed_lines(diff)
            status = click.style(f"⚠  {n} line(s) changed", fg="yellow")
            any_changed = True
        click.echo(f"    {filename:<50} {status}")
        if verbose and diff is not None:
            _print_diff_lines(diff)

    return any_changed


@click.command(
    "product:drift",
    short_help="Show template drift for SPLENT-owned product files.",
)
@click.argument("product_name", required=False)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    help="Show full unified diff for changed files.",
)
def product_drift(product_name, verbose):
    """
    Compare SPLENT-owned files in a product against the current templates.
    Does not modify any files.

    \b
    Examples:
        splent product:drift myapp
        splent product:drift myapp --verbose
    """
    workspace = context.workspace()
    if not product_name:
        product_name = context.require_app()

    product_path = workspace / product_name
    if not product_path.exists():
        click.secho(f"❌ Product '{product_name}' not found.", fg="red")
        raise SystemExit(1)

    stored_ver = get_stored_cli_version(product_path / "pyproject.toml")
    ver_note = (
        f"generated with CLI v{stored_ver}"
        if stored_ver
        else "generated with unknown CLI version"
    )

    click.echo()
    click.echo(
        click.style(
            f"  Drift report for {product_name}"
            f"   ({ver_note}, current CLI v{CLI_VERSION})",
            bold=True,
        )
    )
    click.echo(click.style(f"  {'─' * 72}", fg="bright_black"))

    ctx = product_ctx(product_name)
    any_drift = False

    for group_name, files in PRODUCT_GROUPS.items():
        changed = _check_group(
            product_path, group_name, files, ctx, product_name, verbose
        )
        if changed:
            any_drift = True

    click.echo()
    if any_drift:
        click.secho(
            "  Run 'splent product:sync-template' to apply changes.",
            fg="yellow",
        )
    else:
        click.secho("  ✅ All SPLENT-owned files are up to date.", fg="green")
    click.echo()


cli_command = product_drift
