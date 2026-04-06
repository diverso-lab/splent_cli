import click
from flask import current_app

from splent_cli.services import context
from splent_cli.utils.decorators import requires_app


def _format_flags(cmd) -> str:
    """Extract flags/options from a Click command as a compact string.

    Shows the type hint after each option (e.g. ``--to EMAIL *``).
    A trailing ``*`` marks required options.
    """
    parts = []
    for param in cmd.params:
        if isinstance(param, click.Option):
            name = max(param.opts, key=len)  # prefer --long over -s
            if param.is_flag:
                parts.append(name)
            else:
                # Use explicit metavar, or derive from type name
                hint = param.metavar or param.type.name.upper()
                req = " *" if param.required else ""
                parts.append(f"{name} {hint}{req}")
    return ", ".join(parts)


@requires_app
@click.command(
    "product:commands",
    short_help="List all CLI commands contributed by features.",
)
@context.requires_product
def product_commands():
    """Show CLI commands registered by features in the active product."""
    registry = current_app.extensions.get("splent_feature_commands", {})

    if not registry:
        click.echo()
        click.secho("  No feature commands registered.", fg="yellow")
        click.echo()
        return

    # Build rows: (feature, invocation, flags, description)
    rows = []
    for feature_short, commands in sorted(registry.items()):
        for cmd in sorted(commands, key=lambda c: c.name):
            desc = cmd.get_short_help_str() or "—"
            invocation = f"feature:{feature_short} {cmd.name}"
            flags = _format_flags(cmd)
            rows.append((feature_short, invocation, flags, desc))

    # Column widths based on visible text
    col_feat = max(len(r[0]) for r in rows)
    col_feat = max(col_feat, len("Feature"))
    col_cmd = max(len(r[1]) for r in rows)
    col_cmd = max(col_cmd, len("Command"))
    col_flags = max(len(r[2]) for r in rows)
    col_flags = max(col_flags, len("Flags"))

    click.echo()
    click.secho("  Feature Commands", bold=True, fg="cyan")
    click.echo()

    # Header
    click.echo(
        f"  {'Feature':<{col_feat}}  {'Command':<{col_cmd}}  {'Flags':<{col_flags}}  Description"
    )
    click.echo(f"  {'-' * col_feat}  {'-' * col_cmd}  {'-' * col_flags}  {'-' * 30}")

    # Rows
    prev_feat = None
    for feat, invocation, flags, desc in rows:
        feat_display = feat if feat != prev_feat else ""
        colored_cmd = click.style(invocation, fg="green")
        colored_flags = click.style(flags, fg="yellow") if flags else ""
        # ANSI codes add 9 chars per colored field; pad accordingly
        ansi_cmd = 9 if invocation else 0
        ansi_flags = 9 if flags else 0
        click.echo(
            f"  {feat_display:<{col_feat}}"
            f"  {colored_cmd:<{col_cmd + ansi_cmd}}"
            f"  {colored_flags:<{col_flags + ansi_flags}}"
            f"  {desc}"
        )
        prev_feat = feat

    click.echo()
    click.secho(f"  {len(rows)} command(s) from {len(registry)} feature(s).", dim=True)
    click.echo()


cli_command = product_commands
