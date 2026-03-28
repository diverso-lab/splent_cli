"""
feature:xray — Show the refinement map for the active product.
"""

import os
import tomllib

import click

from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data


def _read_feature_splent(workspace: str, feature_name: str) -> dict:
    """Read [tool.splent] from a feature's pyproject.toml."""
    for candidate_dir in [
        os.path.join(workspace, feature_name),
        os.path.join(workspace, ".splent_cache", "features", "splent_io", feature_name),
    ]:
        pyproject = os.path.join(candidate_dir, "pyproject.toml")
        if os.path.isfile(pyproject):
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            return data.get("tool", {}).get("splent", {})

    # Try versioned entries in cache
    cache_dir = os.path.join(workspace, ".splent_cache", "features", "splent_io")
    if os.path.isdir(cache_dir):
        for entry in sorted(os.listdir(cache_dir)):
            if entry.startswith(feature_name + "@"):
                pyproject = os.path.join(cache_dir, entry, "pyproject.toml")
                if os.path.isfile(pyproject):
                    with open(pyproject, "rb") as f:
                        data = tomllib.load(f)
                    return data.get("tool", {}).get("splent", {})

    return {}


def _bare_name(feature_ref: str) -> str:
    """Extract bare feature name from a ref like 'splent-io/splent_feature_auth@v1.2.7'."""
    clean = compose.normalize_feature_ref(feature_ref)
    name = clean.split("/")[-1] if "/" in clean else clean
    # Strip version
    name = name.split("@")[0]
    return name


@click.command("feature:xray", short_help="Show refinement map for the active product.")
@click.argument("feature_ref", required=False)
@click.option("--services", "filter_cat", flag_value="service", help="Show only services.")
@click.option("--templates", "filter_cat", flag_value="template", help="Show only templates.")
@click.option("--models", "filter_cat", flag_value="model", help="Show only models.")
@click.option("--routes", "filter_cat", flag_value="route", help="Show only routes.")
@click.option("--hooks", "filter_cat", flag_value="hook", help="Show only hooks.")
def feature_xray(feature_ref, filter_cat):
    """Show the refinement and extension map for all features (or a single one)."""
    workspace = str(context.workspace())
    product = context.require_app()
    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        click.secho("❌ pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    env = os.getenv("SPLENT_ENV", "dev")
    features = read_features_from_data(data, env)

    # Collect data for all features
    feature_data = {}  # bare_name -> {extensible, refinement, contract}
    for feat in features:
        name = _bare_name(feat)
        splent = _read_feature_splent(workspace, name)
        feature_data[name] = {
            "extensible": splent.get("contract", {}).get("extensible", {}),
            "refinement": splent.get("refinement", {}),
            "contract": splent.get("contract", {}),
            "entry": feat,
        }

    # Build refinement map
    refinement_map = {}  # base_name -> [{refiner, category, target, replacement}]
    for name, fdata in feature_data.items():
        ref = fdata["refinement"]
        if not ref.get("refines"):
            continue
        base = ref["refines"]
        if base not in refinement_map:
            refinement_map[base] = []

        for category, key in [
            ("service", "services"),
            ("template", "templates"),
            ("hook", "hooks"),
        ]:
            for override in ref.get("overrides", {}).get(key, []):
                refinement_map[base].append({
                    "refiner": name,
                    "category": category,
                    "target": override.get("target", ""),
                    "replacement": override.get("replacement", ""),
                    "action": "override",
                })

        for ext in ref.get("extends", {}).get("models", []):
            refinement_map[base].append({
                "refiner": name,
                "category": "model",
                "target": ext.get("target", ""),
                "replacement": ext.get("mixin", ""),
                "action": "extend",
            })

        for ext in ref.get("extends", {}).get("routes", []):
            refinement_map[base].append({
                "refiner": name,
                "category": "route",
                "target": ext.get("blueprint", ""),
                "replacement": ext.get("module", ""),
                "action": "add",
            })

    # Filter to single feature if requested
    display_features = list(feature_data.keys())
    if feature_ref:
        target = _bare_name(feature_ref)
        if target not in feature_data:
            click.secho(f"❌ Feature '{target}' not found in product.", fg="red")
            raise SystemExit(1)
        display_features = [target]

    # Read product layout hooks
    product_splent = data.get("tool", {}).get("splent", {})
    layout_config = product_splent.get("layout", {})
    layout_hooks = layout_config.get("hooks", [])
    layout_template = layout_config.get("base_template", "")

    # Build hook usage map: hook_name -> [features that register it]
    hook_usage: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        provides_hooks = fdata["contract"].get("provides", {}).get("hooks", [])
        for h in provides_hooks:
            hook_usage.setdefault(h, []).append(name)

    # Display
    click.echo()
    click.echo(click.style(f"  Feature X-Ray — {product}", bold=True))
    click.echo()

    # Product layout section
    if layout_hooks and (not filter_cat or filter_cat == "hook"):
        click.echo(click.style(f"  {product} (product layout)", bold=True))
        if layout_template:
            click.echo(f"    {'template':>10}: {layout_template}")
        for hook_name in layout_hooks:
            users = hook_usage.get(hook_name, [])
            if users:
                user_str = ", ".join(users)
                click.echo(
                    f"    {'hook':>10}: {hook_name}"
                    + click.style(f"  ← {user_str}", fg="green")
                )
            else:
                click.echo(
                    f"    {'hook':>10}: {hook_name}"
                    + click.style("  (unused)", fg="bright_black")
                )
        click.echo()

    for name in display_features:
        data = feature_data[name]
        ext = data["extensible"]
        ref = data["refinement"]
        contract = data["contract"]
        overrides = refinement_map.get(name, [])
        is_refiner = bool(ref.get("refines"))

        # Skip features with no relevance if we have a filter
        if filter_cat and not overrides and not ext and not is_refiner:
            continue

        # Header
        label = name
        if is_refiner:
            label += click.style(f" (refines {ref['refines']})", fg="cyan")
        elif overrides:
            refiners = sorted({o["refiner"] for o in overrides})
            label += click.style(f" (refined by {', '.join(refiners)})", fg="yellow")

        click.echo(click.style(f"  {label}", bold=True))

        # Extensible points
        if ext and not is_refiner:
            for cat in ["services", "templates", "models", "hooks"]:
                items = ext.get(cat, [])
                if not items:
                    continue
                if filter_cat and cat.rstrip("s") != filter_cat:
                    continue
                for item in items:
                    # Check if overridden
                    override = next(
                        (o for o in overrides if o["target"] == item and o["category"] == cat.rstrip("s")),
                        None,
                    )
                    if override:
                        action_icon = "overridden" if override["action"] == "override" else "extended"
                        click.echo(
                            f"    {cat[:-1]:>10}: {item}"
                            + click.style(f"  ← {action_icon} by {override['refiner']}", fg="yellow")
                        )
                    else:
                        click.echo(f"    {cat[:-1]:>10}: {item}" + click.style("  (extensible)", fg="bright_black"))

            if ext.get("routes"):
                route_adds = [o for o in overrides if o["category"] == "route"]
                if route_adds:
                    for r in route_adds:
                        click.echo(
                            f"    {'route':>10}: {r['replacement']}"
                            + click.style(f"  ← added by {r['refiner']}", fg="yellow")
                        )
                elif not filter_cat or filter_cat == "route":
                    click.echo(f"    {'routes':>10}: " + click.style("extensible (no additions)", fg="bright_black"))

        # Show what this feature provides (from contract)
        provides = contract.get("provides", {})
        if not ext and not is_refiner:
            for cat in ["services", "models", "routes", "hooks"]:
                if filter_cat and cat.rstrip("s") != filter_cat:
                    continue
                items = provides.get(cat, [])
                if items:
                    for item in items:
                        click.echo(f"    {cat[:-1]:>10}: {item}")

        # Show refinement contributions
        if is_refiner:
            base = ref["refines"]
            my_overrides = [o for o in refinement_map.get(base, []) if o["refiner"] == name]
            for o in my_overrides:
                if filter_cat and o["category"] != filter_cat:
                    continue
                if o["action"] == "override":
                    click.echo(
                        f"    {o['category']:>10}: {o['target']}"
                        + click.style(f" → {o['replacement']}", fg="green")
                    )
                elif o["action"] == "extend":
                    click.echo(
                        f"    {o['category']:>10}: {o['target']}"
                        + click.style(f" + {o['replacement']}", fg="green")
                    )
                elif o["action"] == "add":
                    click.echo(
                        f"    {o['category']:>10}: {o['target']}"
                        + click.style(f" + {o['replacement']}", fg="green")
                    )

        click.echo()

    # Summary
    total_overrides = sum(len(v) for v in refinement_map.values())
    if total_overrides:
        click.secho(
            f"  {total_overrides} refinement(s) across {len(refinement_map)} feature(s).",
            fg="yellow",
        )
    else:
        click.secho("  No refinements declared.", fg="bright_black")
    click.echo()


cli_command = feature_xray
