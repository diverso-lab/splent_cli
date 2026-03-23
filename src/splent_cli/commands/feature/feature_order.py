"""
splent feature:order

Shows the topological load order for every feature declared in the active
product's pyproject.toml, resolved against the product's UVL constraints.

Each feature is annotated with the features it directly requires (from its
[tool.splent.contract.requires].features).  Independent features keep their
original pyproject.toml order (stable sort).

If no UVL file is found the output preserves the pyproject.toml order and
says so.
"""

import json
import os
import tomllib
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


def _contract_requires(product_dir: str, namespace: str, name: str, version: str | None) -> list[str]:
    """Read [tool.splent.contract.requires].features from a feature's pyproject.toml.

    Tries the versioned symlink name first (e.g. splent_feature_auth@v1.1.1),
    then falls back to the unversioned name.
    """
    features_base = os.path.join(product_dir, "features", namespace)
    candidates = []
    if version:
        candidates.append(os.path.join(features_base, f"{name}@{version}", "pyproject.toml"))
    candidates.append(os.path.join(features_base, name, "pyproject.toml"))

    for pyproject in candidates:
        if not os.path.exists(pyproject):
            continue
        try:
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            return (
                data.get("tool", {})
                .get("splent", {})
                .get("contract", {})
                .get("requires", {})
                .get("features", [])
            )
        except Exception:
            continue
    return []


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
def feature_order(as_json):
    """
    Display the order in which features are loaded (and seeded), resolved by
    UVL dependency constraints.  Features that depend on others always appear
    after their dependencies.

    The 'Requires' column shows direct feature dependencies declared in each
    feature's [tool.splent.contract.requires].features.
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

    # Build output rows
    rows = []
    for i, entry in enumerate(ordered, start=1):
        namespace, name, version = _parse_entry(entry)
        requires = _contract_requires(product_dir, namespace, name, version)
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

    click.echo()
    click.echo(click.style(f"  Feature load order — {product}", bold=True))
    click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))
    click.echo(f"  {'#':<4} {'Feature':<44} Requires")
    click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

    for row in rows:
        pos = click.style(f"{row['position']:<4}", fg="bright_black")
        feature_label = row["feature"]
        requires_label = ", ".join(row["requires"]) if row["requires"] else click.style("—", fg="bright_black")
        click.echo(f"  {pos} {feature_label:<44} {requires_label}")

    click.echo()
    click.echo(uvl_note)
    click.echo()


cli_command = feature_order
