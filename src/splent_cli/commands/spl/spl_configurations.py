import click

from splent_cli.commands.spl.spl_utils import _resolve_spl
from splent_cli.commands.uvl.uvl_utils import (
    list_all_features_from_uvl as _list_all_features_from_uvl,
)
from splent_cli.services import context


@click.command(
    "spl:configurations",
    short_help="Enumerate all valid feature selections from the SPL variability model.",
)
@click.argument("spl_name")
@click.option(
    "--count", is_flag=True, help="Only show the total number of configurations."
)
@click.option(
    "--with-sat",
    is_flag=True,
    help="Force PySAT backend (useful in some environments).",
)
@context.requires_detached
def spl_configs(spl_name, count, with_sat):
    """Show all valid product configurations for the SPL.

    Each configuration is a set of features that satisfies all UVL constraints.
    Core features (mandatory) are highlighted. Dead features (never selectable)
    are reported as warnings.
    """
    name, uvl_path = _resolve_spl(spl_name)
    universe, root_name = _list_all_features_from_uvl(uvl_path)

    from splent_cli.commands.uvl.uvl_utils import _require_flamapy

    _require_flamapy()
    from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel

    fm = FLAMAFeatureModel(uvl_path)

    # Core & dead features
    try:
        core = set(fm.core_features())
    except Exception:
        core = set()
    try:
        dead = set(fm.dead_features())
    except Exception:
        dead = set()

    # Count
    try:
        n = fm.configurations_number(with_sat=bool(with_sat))
    except TypeError:
        n = fm.configurations_number()

    # Header
    click.echo()
    click.echo(f"  SPL      : {name}")
    click.echo(f"  UVL      : {uvl_path}")
    click.echo(f"  Features : {len(universe)}")
    click.echo()
    click.secho(f"  {n} valid configuration(s)", bold=True)
    click.echo()

    # Core features
    optional = sorted(f for f in universe if f not in core and f != root_name)
    core_display = sorted(f for f in core if f != root_name)

    if core_display:
        click.echo(
            "  Core (always present): "
            + click.style(", ".join(core_display), fg="green", bold=True)
        )
    if dead:
        click.echo(
            "  Dead (never selectable): "
            + click.style(", ".join(sorted(dead)), fg="red")
        )
    if optional:
        click.echo("  Optional: " + ", ".join(optional))
    click.echo()

    if count:
        return

    # Guard against very large config spaces
    if n > 200:
        click.secho(
            f"  {n} configurations is too many to list. "
            f"Use --count to see just the total, or add constraints to reduce the space.",
            fg="yellow",
        )
        return

    if n > 50:
        if not click.confirm(f"  List all {n} configurations?", default=False):
            return

    # List configurations
    try:
        configs = fm.configurations()
    except Exception as e:
        click.secho(f"  Could not enumerate configurations: {e}", fg="yellow")
        return

    # Sort configs by size (smallest first) then alphabetically
    parsed = []
    for cfg in configs:
        if isinstance(cfg, str):
            features = sorted(f.strip() for f in cfg.split(","))
        else:
            features = sorted(str(f) for f in cfg)
        # Skip root from display
        features = [f for f in features if f != root_name]
        parsed.append(features)
    parsed.sort(key=lambda fs: (len(fs), fs))

    click.secho(f"  {'#':<4} {'Features':<60} Size", fg="cyan")
    click.echo("  " + "\u2500" * 70)

    for i, features in enumerate(parsed, 1):
        parts = []
        for f in features:
            if f in core:
                parts.append(click.style(f, fg="green", bold=True))
            elif f in dead:
                parts.append(click.style(f, fg="red"))
            else:
                parts.append(click.style(f, fg="yellow"))
        idx = click.style(f"{i:<4}", fg="bright_black")
        feat_str = ", ".join(parts)
        optional_count = len([f for f in features if f not in core])
        size_label = f"+{optional_count}" if optional_count else "base"
        click.echo(f"  {idx} {feat_str:<80} {size_label}")

    click.echo()
    click.echo(
        "  "
        + click.style("core", fg="green", bold=True)
        + "  "
        + click.style("optional", fg="yellow")
        + "  "
        + click.style("dead", fg="red")
    )
    click.echo()


cli_command = spl_configs
