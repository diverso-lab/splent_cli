import click
from pathlib import Path

from splent_cli.services import context, compose
from splent_cli.utils.template_drift import (
    CLI_VERSION,
    FEATURE_FILES,
    count_changed_lines,
    feature_ctx,
    file_diff,
    get_stored_cli_version,
    render_template,
    resolve_feature_rel,
)


def _resolve_cache_path(
    feature_identifier: str, workspace: Path
) -> tuple[Path, str, str] | None:
    """
    Return (cache_path, org_safe, feature_name) for a feature identifier,
    or None if not found.
    """
    _, ns_github, ns_fs, rest = compose.parse_feature_identifier(feature_identifier)
    feature_name = rest.split("@")[0]
    cache_root = workspace / ".splent_cache" / "features"

    # Prefer editable (no @version suffix)
    editable = cache_root / ns_fs / feature_name
    if editable.exists():
        return editable, ns_fs, feature_name

    # Fall back to any versioned snapshot
    ns_dir = cache_root / ns_fs
    if ns_dir.exists():
        for d in sorted(ns_dir.iterdir(), reverse=True):
            if d.is_dir() and d.name.startswith(f"{feature_name}@"):
                return d, ns_fs, feature_name

    return None


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


@click.command(
    "feature:drift",
    short_help="Show template drift for SPLENT-owned feature files.",
)
@click.argument("feature_identifier")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show full unified diff for changed files.",
)
def feature_drift(feature_identifier, verbose):
    """
    Compare SPLENT-owned files in a feature against the current templates.
    Only scaffold files are checked (webpack.config.js, MANIFEST.in, .gitignore).
    Developer-owned source files are never touched.
    Does not modify any files.

    \b
    Examples:
        splent feature:drift splent_feature_auth
        splent feature:drift splent_feature_auth --verbose
    """
    workspace = context.workspace()
    result = _resolve_cache_path(feature_identifier, workspace)
    if result is None:
        click.secho(f"❌ Feature '{feature_identifier}' not found in cache.", fg="red")
        raise SystemExit(1)

    cache_path, org_safe, feature_name = result
    stored_ver = get_stored_cli_version(cache_path / "pyproject.toml")
    ver_note = (
        f"generated with CLI v{stored_ver}"
        if stored_ver
        else "generated with unknown CLI version"
    )

    click.echo()
    click.echo(
        click.style(
            f"  Drift report for {org_safe}/{feature_name}"
            f"   ({ver_note}, current CLI v{CLI_VERSION})",
            bold=True,
        )
    )
    click.echo(click.style(f"  {'─' * 72}", fg="bright_black"))
    click.echo()
    click.echo(
        click.style(
            "  scaffold files  [SPLENT-owned — safe to update]",
            bold=True,
        )
    )

    ctx = feature_ctx(org_safe, feature_name)
    any_drift = False

    for rel_tpl, tpl_name in FEATURE_FILES.items():
        rel_path = resolve_feature_rel(rel_tpl, org_safe, feature_name)
        abs_path = cache_path / rel_path
        expected = render_template(tpl_name, ctx)
        diff = file_diff(abs_path, expected)

        filename = Path(rel_path).name
        if diff is None:
            status = click.style("✔  up to date", fg="green")
        else:
            n = count_changed_lines(diff)
            status = click.style(f"⚠  {n} line(s) changed", fg="yellow")
            any_drift = True

        click.echo(f"    {filename:<50} {status}")
        if verbose and diff is not None:
            _print_diff_lines(diff)

    click.echo()
    if any_drift:
        click.secho(
            "  Run 'splent feature:sync-template' to apply changes.",
            fg="yellow",
        )
    else:
        click.secho("  ✅ All SPLENT-owned files are up to date.", fg="green")
    click.echo()


cli_command = feature_drift
