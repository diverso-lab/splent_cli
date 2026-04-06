"""
splent feature:impact

Shows the full dependency impact of a feature within the active product:
what it requires, what depends on it, and what would break if removed.

All dependency information is resolved from the product's UVL constraints.
"""

import os

import click

from splent_cli.services import context
from splent_cli.commands.feature.feature_order import (
    _uvl_path,
    _build_requires_map,
    _build_reverse_map,
    _closure,
    _parse_entry,
)
from splent_framework.utils.pyproject_reader import PyprojectReader


def _short_name(pkg: str) -> str:
    """splent_feature_auth → auth"""
    return pkg.removeprefix("splent_feature_")


@click.command(
    "feature:impact",
    short_help="Show the dependency impact of adding or removing a feature.",
)
@click.argument("feature_ref", required=True)
def feature_impact(feature_ref):
    """
    Show what FEATURE_REF depends on and what depends on it.

    Reads the product's UVL constraints to build a bidirectional dependency
    graph and shows:

    \b
      1. Dependencies — features this one requires to work.
      2. Dependents   — features that require this one.
      3. Impact       — transitive set of features that would break
                        if this feature were removed.

    FEATURE_REF can be a short name (auth) or full package name
    (splent_feature_auth).

    \b
    Examples:
        splent feature:impact auth
        splent feature:impact splent_feature_mail
    """
    product = context.require_app()
    workspace = str(context.workspace())
    product_dir = os.path.join(workspace, product)

    try:
        reader = PyprojectReader.for_product(product_dir)
        env = os.getenv("SPLENT_ENV")
        features_raw = reader.features_for_env(env)
    except FileNotFoundError:
        click.secho("  pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    uvl = _uvl_path(product_dir)
    if not uvl or not os.path.isfile(uvl):
        click.secho("  No UVL file found — cannot compute impact.", fg="red")
        raise SystemExit(1)

    # Normalize input: accept "auth" or "splent_feature_auth"
    target = feature_ref
    if target.startswith("splent_feature_"):
        target = target[len("splent_feature_") :]
    pkg_target = f"splent_feature_{target}"

    # Build declared feature set
    declared_pkgs = set()
    for entry in features_raw:
        _, name, _ = _parse_entry(entry)
        declared_pkgs.add(name)

    if pkg_target not in declared_pkgs:
        click.secho(f"  '{feature_ref}' is not declared in this product.", fg="red")
        raise SystemExit(1)

    # Build dependency graphs
    requires_map = _build_requires_map(uvl)
    reverse_map = _build_reverse_map(requires_map)

    # Direct dependencies (what I need)
    direct_deps = requires_map.get(pkg_target, [])

    # Full dependency closure (transitive: what I need, and what they need, etc.)
    all_deps = _closure(pkg_target, requires_map, declared_pkgs)

    # Direct dependents (who needs me)
    direct_dependents = reverse_map.get(pkg_target, [])

    # Full reverse closure (transitive: who breaks if I'm removed)
    all_affected = _closure(pkg_target, reverse_map, declared_pkgs)

    # ── Output ────────────────────────────────────────────────────────

    click.echo()
    click.secho(f"  feature:impact — {target}", bold=True)
    click.echo(click.style(f"  Product: {product}", fg="bright_black"))
    click.echo(click.style(f"  UVL:     {os.path.basename(uvl)}", fg="bright_black"))

    # Section 1: What I depend on
    click.echo()
    click.secho("  Depends on", bold=True)

    if not all_deps:
        click.echo(click.style("    (none) — this feature is independent", fg="green"))
    else:
        direct_set = set(direct_deps)
        for dep in all_deps:
            marker = "direct" if dep in direct_set else "transitive"
            icon = "→" if marker == "direct" else "  →"
            color = "cyan" if marker == "direct" else "bright_black"
            click.echo(
                f"    {icon} {click.style(_short_name(dep), fg=color)}  ({marker})"
            )

    # Section 2: Who depends on me + impact
    click.echo()
    click.secho("  Depended on by", bold=True)

    if not all_affected:
        click.echo(click.style("    (none) — safe to remove", fg="green"))
    else:
        direct_set = set(direct_dependents)
        for dep in all_affected:
            marker = "direct" if dep in direct_set else "transitive"
            icon = "←" if marker == "direct" else "  ←"
            color = "red" if marker == "direct" else "yellow"
            click.echo(
                f"    {icon} {click.style(_short_name(dep), fg=color)}  ({marker})"
            )

    # Section 3: Summary
    click.echo()
    if all_affected:
        click.secho(
            f"  Removing {target} would break {len(all_affected)} feature(s).",
            fg="red",
            bold=True,
        )
    else:
        click.secho(
            f"  Removing {target} has no impact on other features.",
            fg="green",
            bold=True,
        )
    click.echo()


cli_command = feature_impact
