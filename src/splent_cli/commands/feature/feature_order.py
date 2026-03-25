"""
splent feature:order

Shows the topological load order for every feature declared in the active
product's pyproject.toml, resolved against the product's UVL constraints.

Each feature is annotated with the features it directly requires, read from
the UVL constraints (primary source).  Independent features keep their
original pyproject.toml order (stable sort).

If no UVL file is found the output preserves the pyproject.toml order and
says so.
"""

import json
import os
from pathlib import Path

import click

from splent_cli.services import context
from splent_framework.managers.feature_order import FeatureLoadOrderResolver
from splent_framework.utils.pyproject_reader import PyprojectReader


def _uvl_path(product_dir: str) -> str | None:
    try:
        uvl_cfg = PyprojectReader.for_product(product_dir).uvl_config
        uvl_file = uvl_cfg.get("file")
        if uvl_file:
            return os.path.join(product_dir, "uvl", uvl_file)
    except Exception:
        pass
    return None


def _build_requires_map(uvl: str | None) -> dict[str, list[str]]:
    """Return {package_name: [required_package_names]} from UVL constraints.

    Uses FeatureLoadOrderResolver's own parsers so the logic stays in one place.
    Short names (e.g. 'profile') are resolved to package names (e.g. 'splent_feature_profile').
    """
    if not uvl or not os.path.isfile(uvl):
        return {}

    text = Path(uvl).read_text(encoding="utf-8", errors="replace")
    pkg_map = FeatureLoadOrderResolver._parse_package_map(text)   # {short → pkg}
    constraints = FeatureLoadOrderResolver._parse_constraints(text)  # [(requirer_short, required_short)]

    result: dict[str, list[str]] = {}
    for requirer_short, required_short in constraints:
        requirer_pkg = pkg_map.get(requirer_short)
        required_pkg = pkg_map.get(required_short)
        if requirer_pkg and required_pkg:
            result.setdefault(requirer_pkg, []).append(required_pkg)
    return result


def _parse_entry(entry: str) -> tuple[str, str, str | None]:
    """Return (namespace, name, version | None) from a pyproject feature entry."""
    base, _, version = entry.partition("@")
    # base is like "splent_feature_auth" or "splent_io/splent_feature_auth"
    if "/" in base:
        namespace, name = base.split("/", 1)
    else:
        namespace = "splent_io"
        name = base
    return namespace, name, version or None


@click.command(
    "feature:order",
    short_help="Show the topological load order of features in the active product.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON array.")
@click.option("--no-namespace", is_flag=True, default=False, help="Hide namespace prefix from feature names.")
@click.option("--no-version", is_flag=True, default=False, help="Hide version suffix from feature names.")
@click.option("--short", is_flag=True, default=False, help="Shorthand for --no-namespace --no-version.")
def feature_order(as_json, no_namespace, no_version, short):
    no_namespace = no_namespace or short
    no_version = no_version or short
    """
    Display the order in which features are loaded (and seeded), resolved by
    UVL dependency constraints.  Features that depend on others always appear
    after their dependencies.

    The 'Requires' column shows direct feature dependencies from the UVL constraints.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    product_dir = os.path.join(workspace, product)

    try:
        features_raw = PyprojectReader.for_product(product_dir).features
    except FileNotFoundError:
        click.secho("❌ pyproject.toml not found in product.", fg="red")
        raise SystemExit(1)

    if not features_raw:
        click.echo("  No features declared in pyproject.toml.")
        return

    uvl = _uvl_path(product_dir)
    ordered = FeatureLoadOrderResolver().resolve(features_raw, uvl)
    requires_map = _build_requires_map(uvl)

    # Build output rows
    rows = []
    for i, entry in enumerate(ordered, start=1):
        namespace, name, version = _parse_entry(entry)
        requires = requires_map.get(name, [])
        rows.append({
            "position": i,
            "feature": entry,
            "namespace": namespace,
            "name": name,
            "version": version,
            "requires": requires,
        })

    if as_json:
        click.echo(json.dumps(rows, indent=2))
        return

    uvl_note = (
        click.style(f"  UVL: {os.path.basename(uvl)}", fg="bright_black")
        if uvl and os.path.isfile(uvl)
        else click.style("  No UVL found — preserving pyproject.toml order", fg="yellow")
    )

    def _format_feature(row: dict) -> str:
        label = row["name"]
        if not no_namespace and row["namespace"]:
            label = f"{row['namespace']}/{label}"
        if not no_version and row["version"]:
            label = f"{label}@{row['version']}"
        return label

    col_w = 34 if (no_namespace or no_version) else 44
    sep_w = col_w + 12

    click.echo()
    click.echo(click.style(f"  Feature load order — {product}", bold=True))
    click.echo(click.style(f"  {'─' * sep_w}", fg="bright_black"))
    click.echo(f"  {'#':<4} {'Feature':<{col_w}} Requires")
    click.echo(click.style(f"  {'─' * sep_w}", fg="bright_black"))

    for row in rows:
        pos = click.style(f"{row['position']:<4}", fg="bright_black")
        feature_label = _format_feature(row)
        requires_label = ", ".join(row["requires"]) if row["requires"] else click.style("—", fg="bright_black")
        click.echo(f"  {pos} {feature_label:<{col_w}} {requires_label}")

    click.echo()
    click.echo(uvl_note)
    click.echo()


cli_command = feature_order
