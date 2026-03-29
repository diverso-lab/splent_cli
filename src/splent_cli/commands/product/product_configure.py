"""
product:configure — Interactive SPL feature configurator.

Reads the UVL from the SPL catalog, presents mandatory/optional features,
propagates constraints in real time, and attaches selected features to the product.
"""

import os
import tomllib

import click

from splent_cli.services import context


def _load_spl_model(catalog_dir: str, spl_name: str):
    """Load SPL feature model and return (fm_object, root, features_info, constraints)."""
    from flamapy.core.discover import DiscoverMetamodels

    uvl_path = os.path.join(catalog_dir, spl_name, f"{spl_name}.uvl")
    if not os.path.isfile(uvl_path):
        raise click.ClickException(f"UVL not found: {uvl_path}")

    dm = DiscoverMetamodels()
    fm = dm.use_transformation_t2m(uvl_path, "fm")
    root = fm.root

    # Extract feature info
    features = []
    for child in root.get_children():
        attrs = {}
        for attr in child.get_attributes():
            attrs[attr.name] = attr.default_value
        features.append({
            "name": child.name,
            "mandatory": child.is_mandatory(),
            "org": attrs.get("org", "splent-io").strip("'\""),
            "package": attrs.get("package", "").strip("'\""),
        })

    # Extract implication constraints (A => B)
    constraints = []
    for c in fm.get_constraints():
        s = str(c)
        if "IMPLIES" in s:
            # Parse "(Constraint N) IMPLIES[A][B]"
            parts = s.split("IMPLIES")
            if len(parts) == 2:
                rest = parts[1]
                names = [n.strip("[] ") for n in rest.split("][")]
                if len(names) == 2:
                    constraints.append((names[0], names[1]))

    return fm, root.name, features, constraints


def _propagate(selected: set[str], mandatory: set[str], constraints: list[tuple[str, str]]) -> set[str]:
    """Propagate constraints: if A is selected and A=>B, select B too."""
    result = set(selected) | mandatory
    changed = True
    while changed:
        changed = False
        for src, dst in constraints:
            if src in result and dst not in result:
                result.add(dst)
                changed = True
    return result


def _get_auto_selected(feature_name: str, mandatory: set[str], constraints: list[tuple[str, str]]) -> set[str]:
    """Return features that would be auto-selected if feature_name is toggled on."""
    return _propagate({feature_name}, mandatory, constraints) - mandatory - {feature_name}


@click.command(
    "product:configure",
    short_help="Interactive feature configurator for the active product.",
)
def product_configure():
    """
    Interactive SPL feature configurator.

    \b
    Reads the UVL model from the SPL catalog, shows mandatory and optional
    features, propagates constraints, and attaches the selected configuration
    to the product.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    if not os.path.isfile(pyproject_path):
        raise click.ClickException(f"Product not found: {product}")

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    spl_name = data.get("tool", {}).get("splent", {}).get("spl")
    if not spl_name:
        raise click.ClickException(
            "No SPL configured. Set [tool.splent].spl in pyproject.toml"
        )

    catalog_dir = os.path.join(workspace, "splent_catalog")
    fm, root_name, features, constraints = _load_spl_model(catalog_dir, spl_name)

    # Classify features
    mandatory_features = {f["name"] for f in features if f["mandatory"]}
    optional_features = [f for f in features if not f["mandatory"]]
    feature_map = {f["name"]: f for f in features}

    click.echo()
    click.echo(click.style(f"  🧬 SPL Configurator — {spl_name}", bold=True))
    click.echo(click.style(f"  {'─' * 55}", fg="bright_black"))
    click.echo()

    # Show mandatory
    click.echo(click.style("  Mandatory (auto-selected):", fg="green"))
    for f in features:
        if f["mandatory"]:
            click.echo(f"    ✅ {f['name']}")
    click.echo()

    # Interactive selection of optional features
    selected: set[str] = set()

    if not optional_features:
        click.echo("  No optional features available.")
    else:
        click.echo(click.style("  Optional features:", bold=True))
        click.echo()

        for f in optional_features:
            name = f["name"]

            # Show what would be auto-selected
            auto = _get_auto_selected(name, mandatory_features, constraints)
            deps_str = ""
            if auto:
                deps_labels = []
                for dep in sorted(auto):
                    if dep in selected:
                        deps_labels.append(click.style(f"{dep} ✅", fg="green"))
                    else:
                        deps_labels.append(click.style(f"+ {dep}", fg="yellow"))
                deps_str = f"  (requires: {', '.join(deps_labels)})"

            if name in selected:
                # Already auto-selected by a previous choice
                click.echo(f"    ✅ {name}" + click.style("  (auto-selected by dependency)", fg="cyan"))
                continue

            if click.confirm(f"    Include {click.style(name, bold=True)}?{deps_str}", default=False):
                selected.add(name)
                # Propagate
                propagated = _propagate(selected, mandatory_features, constraints)
                auto_added = propagated - selected - mandatory_features
                if auto_added:
                    for auto_name in sorted(auto_added):
                        click.echo(
                            f"      ↳ {auto_name}"
                            + click.style("  (auto-selected: required dependency)", fg="cyan")
                        )
                selected = propagated - mandatory_features

    # Final configuration
    all_selected = mandatory_features | selected
    click.echo()
    click.echo(click.style("  ─── Configuration ───", bold=True))
    click.echo()
    for f in features:
        name = f["name"]
        if name in mandatory_features:
            click.echo(f"    ✅ {name}" + click.style("  (mandatory)", fg="bright_black"))
        elif name in selected:
            click.echo(f"    ✅ {name}")
        else:
            click.echo(click.style(f"    ⬜ {name}", fg="bright_black"))

    click.echo()

    # Validate with Flamapy
    click.echo("  Validating configuration... ", nl=False)
    from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel
    from splent_cli.commands.uvl.uvl_utils import (
        list_all_features_from_uvl,
        write_csvconf_full,
    )

    uvl_path = os.path.join(catalog_dir, spl_name, f"{spl_name}.uvl")
    universe, _ = list_all_features_from_uvl(uvl_path)
    selected_set = all_selected | {root_name}
    conf_path = write_csvconf_full(universe, selected_set)

    try:
        fma = FLAMAFeatureModel(uvl_path)
        ok = fma.satisfiable_configuration(conf_path, full_configuration=False)
    finally:
        try:
            os.remove(conf_path)
        except OSError:
            pass

    if not ok:
        click.secho("❌ UNSATISFIABLE", fg="red")
        click.echo("  The selected configuration violates UVL constraints.")
        raise SystemExit(1)

    click.secho("✅ satisfiable", fg="green")
    click.echo()

    # Confirm and attach
    if not click.confirm("  Apply this configuration? (attach features to product)"):
        click.echo("  ❎ Cancelled.")
        return

    click.echo()

    # Clear existing features from pyproject before applying new selection
    import tomli_w
    from splent_cli.utils.feature_utils import write_features_to_data

    with open(pyproject_path, "rb") as f:
        pydata = tomllib.load(f)

    write_features_to_data(pydata, [])
    with open(pyproject_path, "wb") as f:
        tomli_w.dump(pydata, f)

    # Clean stale symlinks
    features_dir = os.path.join(product_path, "features")
    if os.path.isdir(features_dir):
        for org_dir in os.listdir(features_dir):
            org_path = os.path.join(features_dir, org_dir)
            if not os.path.isdir(org_path):
                continue
            for entry in os.listdir(org_path):
                entry_path = os.path.join(org_path, entry)
                if os.path.islink(entry_path):
                    os.unlink(entry_path)

    click.echo("  🧹 Cleared existing feature list.")

    # Write selected features to pyproject and create symlinks
    cache_dir = os.path.join(workspace, ".splent_cache", "features")

    import subprocess
    from splent_cli.utils.feature_utils import write_features_to_data

    # Build the feature list with versions (from cache or PyPI)
    feature_entries = []
    for f in features:
        name = f["name"]
        if name not in all_selected:
            continue

        package = f["package"]
        org = f["org"]
        if not package:
            continue

        # Try cache first
        org_safe = org.replace("-", "_")
        org_cache = os.path.join(cache_dir, org_safe)
        version = None
        if os.path.isdir(org_cache):
            candidates = sorted(
                [e for e in os.listdir(org_cache) if e.startswith(f"{package}@")],
                reverse=True,
            )
            if candidates:
                version = candidates[0].split("@", 1)[1]

        # Fallback: query PyPI
        if not version:
            try:
                result = subprocess.run(
                    ["pip", "index", "versions", package],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0 and "(" in result.stdout:
                    pypi_ver = result.stdout.split("(")[1].split(")")[0].strip()
                    version = f"v{pypi_ver}"
            except Exception:
                pass

        if version:
            entry = f"{org}/{package}@{version}"
        else:
            entry = f"{org}/{package}"
        feature_entries.append(entry)
        click.echo(f"  📦 {entry}")

    # Write features to pyproject
    with open(pyproject_path, "rb") as pf:
        current_data = tomllib.load(pf)

    write_features_to_data(current_data, feature_entries)
    import tomli_w
    with open(pyproject_path, "wb") as pf:
        tomli_w.dump(current_data, pf)

    click.echo()
    click.secho("  ✅ Configuration applied.", fg="green")
    click.echo()
    click.echo("  Next: download and link features with:")
    click.echo("     splent product:sync")
    click.echo()


cli_command = product_configure
