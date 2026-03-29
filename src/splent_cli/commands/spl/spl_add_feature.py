"""
spl:add-feature — Add a feature to an SPL with auto-detected dependency constraints.
"""

import os
import re

import click

from splent_cli.services import context
from splent_cli.commands.spl.spl_utils import _resolve_spl


def _parse_uvl_packages(uvl_path: str) -> dict[str, str]:
    """Return {short_name: package_name} from UVL feature declarations."""
    with open(uvl_path) as f:
        text = f.read()
    result = {}
    for m in re.finditer(r"(\w+)\s*\{([^}]*)\}", text):
        short = m.group(1)
        pkg_m = re.search(r"package\s+'([^']+)'", m.group(2))
        if pkg_m:
            result[short] = pkg_m.group(1)
    return result


def _build_table_to_feature(workspace: str, package_map: dict[str, str]) -> dict[str, str]:
    """Scan all features' models.py and return {table_name: short_name}.

    Uses SQLAlchemy convention: class MyModel → table 'my_model'.
    """
    table_map = {}
    for short, pkg in package_map.items():
        models_candidates = [
            os.path.join(workspace, pkg, "src", "splent_io", pkg, "models.py"),
            os.path.join(workspace, ".splent_cache", "features", "splent_io"),
        ]
        # Check workspace root
        models_path = os.path.join(workspace, pkg, "src", "splent_io", pkg, "models.py")
        if not os.path.isfile(models_path):
            # Check any cached version
            cache_org = os.path.join(workspace, ".splent_cache", "features", "splent_io")
            if os.path.isdir(cache_org):
                for entry in sorted(os.listdir(cache_org)):
                    if entry.startswith(f"{pkg}@"):
                        candidate = os.path.join(cache_org, entry, "src", "splent_io", pkg, "models.py")
                        if os.path.isfile(candidate):
                            models_path = candidate
                            break

        if not os.path.isfile(models_path):
            continue

        with open(models_path) as f:
            content = f.read()

        # Find class names inheriting db.Model
        for cls_name in re.findall(r"class\s+(\w+)\s*\([^)]*db\.Model[^)]*\)", content):
            # SQLAlchemy convention: CamelCase → snake_case table name
            table = re.sub(r"(?<!^)(?=[A-Z])", "_", cls_name).lower()
            table_map[table] = short

    return table_map


def _scan_feature_deps(
    feature_path: str,
    feature_pkg: str,
    all_packages: set[str],
    table_to_feature: dict[str, str],
    pkg_to_short: dict[str, str],
) -> set[str]:
    """Scan a feature's source for dependencies. Returns set of short_names."""
    deps = set()
    src_dir = os.path.join(feature_path, "src", "splent_io", feature_pkg)
    if not os.path.isdir(src_dir):
        return deps

    for root, _, files in os.walk(src_dir):
        for f in files:
            filepath = os.path.join(root, f)
            try:
                with open(filepath, "r", encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                continue

            if f.endswith(".py"):
                # Python imports
                for match in re.findall(r"(?:from|import)\s+splent_io\.(splent_feature_\w+)", content):
                    if match != feature_pkg and match in all_packages:
                        short = pkg_to_short.get(match)
                        if short:
                            deps.add(short)

                # FK references: db.ForeignKey("table.column")
                for fk_ref in re.findall(r'db\.ForeignKey\s*\(\s*["\'](\w+)\.\w+["\']', content):
                    owner = table_to_feature.get(fk_ref)
                    if owner:
                        deps.add(owner)

            elif f.endswith(".html"):
                # Template url_for references
                for bp_name in re.findall(r"url_for\s*\(\s*['\"](\w+)\.", content):
                    candidate_pkg = f"splent_feature_{bp_name}"
                    if candidate_pkg in all_packages and candidate_pkg != feature_pkg:
                        short = pkg_to_short.get(candidate_pkg)
                        if short:
                            deps.add(short)

    return deps


@click.command(
    "spl:add-feature",
    short_help="Add a feature to an SPL with auto-detected constraints.",
)
@click.argument("spl_name")
@click.argument("feature_package")
@click.option("--org", default="splent-io", help="Feature organization (default: splent-io).")
@context.requires_detached
def spl_add_feature(spl_name, feature_package, org):
    """Add a feature to an SPL, scanning its code for dependency constraints.

    \b
    Example:
      splent spl:add-feature sample_splent_spl splent_feature_notes
    """
    workspace = str(context.workspace())
    _, uvl_path = _resolve_spl(spl_name)

    # Parse existing UVL
    package_map = _parse_uvl_packages(uvl_path)
    pkg_to_short = {v: k for k, v in package_map.items()}
    all_packages = set(package_map.values())

    # Derive short name
    short_name = feature_package
    if short_name.startswith("splent_feature_"):
        short_name = short_name[len("splent_feature_"):]

    # Check if already declared
    if feature_package in all_packages:
        click.secho(f"  ℹ️  {short_name} is already declared in {spl_name}.", fg="yellow")
        return

    click.echo()
    click.echo(click.style(f"  Adding {short_name} to {spl_name}", bold=True))
    click.echo()

    # Build table→feature map for FK detection
    table_to_feature = _build_table_to_feature(workspace, package_map)

    # Scan feature for dependencies
    feature_path = os.path.join(workspace, feature_package)
    if not os.path.isdir(feature_path):
        click.secho(f"  ❌ Feature not found at workspace root: {feature_path}", fg="red")
        raise SystemExit(1)

    click.echo("  Scanning for dependencies...")
    detected_deps = _scan_feature_deps(
        feature_path, feature_package, all_packages, table_to_feature, pkg_to_short
    )

    if detected_deps:
        click.echo()
        click.echo("  Detected dependencies:")
        for dep in sorted(detected_deps):
            click.echo(f"    {short_name} => {dep}")
        click.echo()
    else:
        click.echo("  No dependencies detected.")
        click.echo()

    # Confirm
    if not click.confirm(f"  Add '{short_name}' to {spl_name}?", default=True):
        click.echo("  ❎ Cancelled.")
        return

    # Write to UVL
    with open(uvl_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    # Find the line before "constraints" to insert the feature
    insert_idx = None
    constraints_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "constraints":
            constraints_idx = i
            insert_idx = i
            break

    if insert_idx is None:
        insert_idx = len(lines)

    # Insert feature declaration
    feature_line = f"\t\toptional\n\t\t\t{short_name} {{org '{org}', package '{feature_package}'}}"
    lines.insert(insert_idx, feature_line)

    # Insert constraints after "constraints" line
    if detected_deps and constraints_idx is not None:
        # constraints_idx shifted by 1 due to insertion above
        for dep in sorted(detected_deps):
            constraints_idx += 2  # +1 for feature line (2 lines), +1 for each constraint
            lines.insert(constraints_idx, f"\t{short_name} => {dep}")

    with open(uvl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    click.echo()
    click.secho(f"  ✅ Feature '{short_name}' added to {spl_name}.", fg="green")
    if detected_deps:
        click.echo(f"     Constraints: {', '.join(f'{short_name} => {d}' for d in sorted(detected_deps))}")
    click.echo()


cli_command = spl_add_feature
