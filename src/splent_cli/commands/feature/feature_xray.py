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


# ── Helpers for collecting active items ──────────────────────────────


def _active_items_for_feature(name, ext, overrides, hook_usage, full):
    """Return list of (category_singular, item, annotation) for items to display."""
    lines = []
    for cat in ["services", "templates", "models", "hooks"]:
        items = ext.get(cat, [])
        if not items:
            continue
        cat_s = cat[:-1]
        for item in items:
            override = next(
                (
                    o
                    for o in overrides
                    if o["target"] == item and o["category"] == cat_s
                ),
                None,
            )
            if override:
                action = (
                    "overridden"
                    if override["action"] == "override"
                    else "extended"
                )
                lines.append((cat_s, item, ("override", f"{action} by {override['refiner']}")))
            elif cat == "hooks" and item in hook_usage:
                providers = [f for f in hook_usage[item] if f != name]
                if providers:
                    lines.append((cat_s, item, ("hook", ", ".join(providers))))
                elif full:
                    lines.append((cat_s, item, ("extensible", None)))
            elif full:
                lines.append((cat_s, item, ("extensible", None)))

    # Routes
    if ext.get("routes"):
        route_adds = [o for o in overrides if o["category"] == "route"]
        if route_adds:
            for r in route_adds:
                lines.append(("route", r["replacement"], ("override", f"added by {r['refiner']}")))
        elif full:
            lines.append(("routes", "", ("extensible_routes", None)))

    return lines


# ── Command ──────────────────────────────────────────────────────────


@click.command("feature:xray", short_help="Show refinement map for the active product.")
@click.argument("feature_ref", required=False)
@click.option(
    "--services", "filter_cat", flag_value="service", help="Show only services."
)
@click.option(
    "--templates", "filter_cat", flag_value="template", help="Show only templates."
)
@click.option("--models", "filter_cat", flag_value="model", help="Show only models.")
@click.option("--routes", "filter_cat", flag_value="route", help="Show only routes.")
@click.option("--hooks", "filter_cat", flag_value="hook", help="Show only hooks.")
@click.option(
    "--commands", "filter_cat", flag_value="command", help="Show only commands."
)
@click.option("--signals", "filter_cat", flag_value="signal", help="Show only signals.")
@click.option(
    "--validate", is_flag=True, help="Validate extensibility (detect conflicts)."
)
@click.option(
    "--full", is_flag=True, help="Show all extensible points (not just active ones)."
)
def feature_xray(feature_ref, filter_cat, validate, full):
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

    # Check for stale contracts
    from splent_cli.utils.contract_freshness import check_and_refresh_contracts

    check_and_refresh_contracts(workspace, features)

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
                refinement_map[base].append(
                    {
                        "refiner": name,
                        "category": category,
                        "target": override.get("target", ""),
                        "replacement": override.get("replacement", ""),
                        "action": "override",
                    }
                )

        for ext in ref.get("extends", {}).get("models", []):
            refinement_map[base].append(
                {
                    "refiner": name,
                    "category": "model",
                    "target": ext.get("target", ""),
                    "replacement": ext.get("mixin", ""),
                    "action": "extend",
                }
            )

        for ext in ref.get("extends", {}).get("routes", []):
            refinement_map[base].append(
                {
                    "refiner": name,
                    "category": "route",
                    "target": ext.get("blueprint", ""),
                    "replacement": ext.get("module", ""),
                    "action": "add",
                }
            )

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

        used_layout = []
        unused_layout = []
        for hook_name in layout_hooks:
            users = hook_usage.get(hook_name, [])
            if users:
                used_layout.append((hook_name, users))
            else:
                unused_layout.append(hook_name)

        for hook_name, users in used_layout:
            user_str = ", ".join(users)
            click.echo(
                f"    {'hook':>10}: {hook_name}"
                + click.style(f"  ← {user_str}", fg="green")
            )

        if unused_layout:
            if full:
                for hook_name in unused_layout:
                    click.echo(
                        f"    {'hook':>10}: {hook_name}"
                        + click.style("  (unused)", fg="bright_black")
                    )
            else:
                click.echo(
                    click.style(
                        f"    {'':>10}  {len(unused_layout)} unused: {', '.join(unused_layout)}",
                        fg="bright_black",
                    )
                )
        click.echo()

    # Feature sections
    quiet_features = []

    for name in display_features:
        fdata = feature_data[name]
        ext = fdata["extensible"]
        ref = fdata["refinement"]
        contract = fdata["contract"]
        overrides = refinement_map.get(name, [])
        is_refiner = bool(ref.get("refines"))

        # Skip features with no relevance if we have a filter
        if filter_cat and not overrides and not ext and not is_refiner:
            continue

        # Collect active items
        active_lines = _active_items_for_feature(name, ext, overrides, hook_usage, full)

        # Apply category filter
        if filter_cat:
            active_lines = [
                (c, i, a) for c, i, a in active_lines if c == filter_cat
            ]

        # Refiner contributions
        refiner_lines = []
        if is_refiner:
            base = ref["refines"]
            my_overrides = [
                o for o in refinement_map.get(base, []) if o["refiner"] == name
            ]
            for o in my_overrides:
                if filter_cat and o["category"] != filter_cat:
                    continue
                refiner_lines.append(o)

        # Decide if this feature has anything to show
        has_content = bool(active_lines or refiner_lines or is_refiner or overrides)

        if not has_content and not full:
            quiet_features.append(name)
            continue

        # Header
        label = name
        if is_refiner:
            label += click.style(f" (refines {ref['refines']})", fg="cyan")
        elif overrides:
            refiners = sorted({o["refiner"] for o in overrides})
            label += click.style(f" (refined by {', '.join(refiners)})", fg="yellow")

        click.echo(click.style(f"  {label}", bold=True))

        # Active extensible items
        if ext and not is_refiner:
            for cat_s, item, (kind, detail) in active_lines:
                if kind == "override":
                    click.echo(
                        f"    {cat_s:>10}: {item}"
                        + click.style(f"  ← {detail}", fg="yellow")
                    )
                elif kind == "hook":
                    click.echo(
                        f"    {cat_s:>10}: {item}"
                        + click.style(f"  ← {detail}", fg="green")
                    )
                elif kind == "extensible":
                    click.echo(
                        f"    {cat_s:>10}: {item}"
                        + click.style("  (extensible)", fg="bright_black")
                    )
                elif kind == "extensible_routes":
                    click.echo(
                        f"    {'routes':>10}: "
                        + click.style("extensible (no additions)", fg="bright_black")
                    )

        # Non-extensible feature provides (from contract)
        provides = contract.get("provides", {})
        if not ext and not is_refiner:
            for cat in ["services", "models", "routes", "hooks", "commands", "signals"]:
                if filter_cat and cat.rstrip("s") != filter_cat:
                    continue
                items = provides.get(cat, [])
                if items:
                    for item in items:
                        click.echo(f"    {cat[:-1]:>10}: {item}")

        # Refinement contributions
        for o in refiner_lines:
            if o["action"] == "override":
                click.echo(
                    f"    {o['category']:>10}: {o['target']}"
                    + click.style(f" → {o['replacement']}", fg="green")
                )
            elif o["action"] in ("extend", "add"):
                click.echo(
                    f"    {o['category']:>10}: {o['target']}"
                    + click.style(f" + {o['replacement']}", fg="green")
                )

        click.echo()

    # Quiet features
    if quiet_features and not feature_ref:
        names = ", ".join(quiet_features)
        click.secho(
            f"  {names} — no active refinements",
            fg="bright_black",
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

    # ── Validate mode ─────────────────────────────────────────────────
    if not validate:
        return

    click.echo(click.style("  ── Validation ──", bold=True))
    click.echo()

    ok = 0
    fail = 0
    info_lines = []

    # 1. Services: check for name collisions
    all_services: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        for svc in fdata["contract"].get("provides", {}).get("services", []):
            all_services.setdefault(svc, []).append(name)

    svc_collisions = {s: fs for s, fs in all_services.items() if len(fs) > 1}
    for svc, features_list in svc_collisions.items():
        is_refinement = any(
            o["target"] == svc and o["category"] == "service"
            for overrides_list in refinement_map.values()
            for o in overrides_list
        )
        if is_refinement:
            ok += 1
        else:
            click.secho(
                f"  ❌ Service '{svc}' — collision: {', '.join(features_list)}",
                fg="red",
            )
            fail += 1
    if not svc_collisions:
        ok += 1

    # 2. Models: check for name collisions
    all_models: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        for model in fdata["contract"].get("provides", {}).get("models", []):
            all_models.setdefault(model, []).append(name)

    model_collisions = {m: fs for m, fs in all_models.items() if len(fs) > 1}
    for model, features_list in model_collisions.items():
        click.secho(
            f"  ❌ Model '{model}' — collision: {', '.join(features_list)}",
            fg="red",
        )
        fail += 1
    if not model_collisions:
        ok += 1

    # 3. Routes: check for collisions
    all_routes: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        for route in fdata["contract"].get("provides", {}).get("routes", []):
            all_routes.setdefault(route, []).append(name)

    route_collisions = {r: fs for r, fs in all_routes.items() if len(fs) > 1}
    for route, features_list in route_collisions.items():
        click.secho(
            f"  ❌ Route '{route}' — collision: {', '.join(features_list)}",
            fg="red",
        )
        fail += 1
    if not route_collisions:
        ok += 1

    # 4. Blueprints: check for name collisions
    all_bps: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        for bp in fdata["contract"].get("provides", {}).get("blueprints", []):
            all_bps.setdefault(bp, []).append(name)

    bp_collisions = {b: fs for b, fs in all_bps.items() if len(fs) > 1}
    for bp, features_list in bp_collisions.items():
        click.secho(
            f"  ❌ Blueprint '{bp}' — collision: {', '.join(features_list)}",
            fg="red",
        )
        fail += 1
    if not bp_collisions:
        ok += 1

    # 5. Commands: check for name collisions
    all_commands: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        for cmd in fdata["contract"].get("provides", {}).get("commands", []):
            all_commands.setdefault(cmd, []).append(name)

    cmd_collisions = {c: fs for c, fs in all_commands.items() if len(fs) > 1}
    for cmd, features_list in cmd_collisions.items():
        click.secho(
            f"  ❌ Command '{cmd}' — collision: {', '.join(features_list)}",
            fg="red",
        )
        fail += 1
    if not cmd_collisions:
        ok += 1

    # 6. Signals: check for name collisions (emitters)
    all_signals: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        for sig in fdata["contract"].get("provides", {}).get("signals", []):
            all_signals.setdefault(sig, []).append(name)

    sig_collisions = {s: fs for s, fs in all_signals.items() if len(fs) > 1}
    for sig, features_list in sig_collisions.items():
        click.secho(
            f"  ❌ Signal '{sig}' — collision: {', '.join(features_list)}", fg="red"
        )
        fail += 1
    if not sig_collisions:
        ok += 1

    # 7. Hooks: shared slots
    shared_hooks: dict[str, list[str]] = {}
    for name, fdata in feature_data.items():
        for hook in fdata["contract"].get("provides", {}).get("hooks", []):
            shared_hooks.setdefault(hook, []).append(name)

    multi_hooks = {h: fs for h, fs in shared_hooks.items() if len(fs) > 1}
    if multi_hooks:
        info_lines.append(f"{len(multi_hooks)} shared hook(s)")
    ok += 1

    # 8. Layout hooks: check for unused slots
    unused_hooks = [h for h in layout_hooks if h not in hook_usage]
    used_hooks = [h for h in layout_hooks if h in hook_usage]
    if unused_hooks:
        info_lines.append(f"{len(unused_hooks)} unused layout hook(s)")
    ok += 1

    # 9. Refinements: validate targets
    for base_name, overrides_list in refinement_map.items():
        base_ext = feature_data.get(base_name, {}).get("extensible", {})
        for o in overrides_list:
            cat_key = (
                o["category"] + "s"
                if not o["category"].endswith("s")
                else o["category"]
            )
            extensible_list = base_ext.get(cat_key, [])
            if o["category"] == "route":
                if not base_ext.get("routes", False):
                    click.secho(
                        f"  ❌ {o['refiner']} adds routes to {base_name} — not declared extensible",
                        fg="red",
                    )
                    fail += 1
                else:
                    ok += 1
            elif o["target"] in extensible_list:
                ok += 1
            else:
                click.secho(
                    f"  ❌ {o['refiner']} overrides {base_name}/{o['target']} — NOT declared extensible",
                    fg="red",
                )
                fail += 1

    # Final summary
    if fail:
        click.secho(
            f"  {fail} issue(s) found, {ok} passed.", fg="red"
        )
        raise SystemExit(1)
    else:
        summary = f"  ✅ All {ok} checks passed."
        if info_lines:
            summary += click.style(f"  ({', '.join(info_lines)})", fg="bright_black")
        click.secho(summary, fg="green")
    click.echo()


cli_command = feature_xray
