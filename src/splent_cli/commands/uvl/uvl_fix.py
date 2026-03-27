"""
uvl:fix — Interactive wizard to add missing UVL constraints based on code analysis.

Cross-checks feature imports against existing UVL constraints and offers
to add the missing ones interactively.
"""

import os
import re

import click

from splent_cli.services import context
from splent_framework.utils.pyproject_reader import PyprojectReader


# ---------------------------------------------------------------------------
# UVL parser
# ---------------------------------------------------------------------------


def _parse_uvl(uvl_path: str) -> tuple[dict[str, str], list[str], str]:
    """Return (package_map, existing_constraints, raw_text)."""
    with open(uvl_path, "r", encoding="utf-8") as f:
        text = f.read()

    package_map: dict[str, str] = {}
    constraints: list[str] = []

    in_features = False
    in_constraints = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "features":
            in_features, in_constraints = True, False
            continue
        if stripped == "constraints":
            in_features, in_constraints = False, True
            continue
        if in_features:
            m = re.match(r"(\w+)\s*\{([^}]*)\}", stripped)
            if m:
                pkg_m = re.search(r"package\s+'([^']+)'", m.group(2))
                if pkg_m:
                    package_map[m.group(1)] = pkg_m.group(1)
        if in_constraints and "=>" in stripped:
            clean = stripped.split("#")[0].strip()
            constraints.append(clean)

    return package_map, constraints, text


def _parse_constraint(c: str) -> tuple[str, str]:
    parts = c.split("=>")
    return parts[0].strip(), parts[1].strip()


# ---------------------------------------------------------------------------
# Source scanner
# ---------------------------------------------------------------------------


def _scan_imports(
    feature_path: str, feature_name: str, all_packages: set[str]
) -> set[str]:
    """Return set of other feature packages imported by this feature."""
    imported: set[str] = set()
    src_dir = None
    for org_dir in os.listdir(os.path.join(feature_path, "src")):
        candidate = os.path.join(feature_path, "src", org_dir, feature_name)
        if os.path.isdir(candidate):
            src_dir = candidate
            break
    if not src_dir:
        return imported

    for root, _, files in os.walk(src_dir):
        for f in files:
            if not f.endswith(".py"):
                continue
            try:
                with open(os.path.join(root, f), "r", encoding="utf-8") as fh:
                    content = fh.read()
            except Exception:
                continue
            for match in re.findall(
                r"(?:from|import)\s+splent_io\.(splent_feature_\w+)", content
            ):
                if match != feature_name and match in all_packages:
                    imported.add(match)

    return imported


def _resolve_feature_paths(workspace: str, product: str) -> dict[str, str]:
    result = {}
    features_dir = os.path.join(workspace, product, "features")
    if not os.path.isdir(features_dir):
        return result
    for org_dir in os.listdir(features_dir):
        org_path = os.path.join(features_dir, org_dir)
        if not os.path.isdir(org_path):
            continue
        for entry in os.listdir(org_path):
            entry_path = os.path.abspath(os.path.join(org_path, entry))
            name = entry.split("@")[0]
            if os.path.isdir(entry_path):
                result[name] = entry_path
    return result


# ---------------------------------------------------------------------------
# UVL writer
# ---------------------------------------------------------------------------


def _write_constraints(
    uvl_path: str, raw_text: str, new_constraints: list[str]
) -> None:
    """Append new constraints to the UVL file."""
    lines = raw_text.rstrip().splitlines()

    # Find the last constraint line or the "constraints" header
    insert_idx = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if "=>" in lines[i] or lines[i].strip() == "constraints":
            insert_idx = i + 1
            break

    for c in new_constraints:
        lines.insert(insert_idx, f"\t{c}")
        insert_idx += 1

    with open(uvl_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command(
    "uvl:fix",
    short_help="Interactive wizard to add missing UVL constraints from code analysis.",
)
def uvl_fix():
    """
    Scans feature source code for cross-feature imports, compares with
    existing UVL constraints, and offers to add the missing ones.

    \b
    For each missing constraint, shows:
      - Which feature imports which
      - Whether it's an undeclared dependency or an inverted one
      - Lets you choose: add the constraint, skip, or mark as inverted (needs refactoring)
    """
    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    # Read UVL
    try:
        uvl_cfg = PyprojectReader.for_product(product_dir).uvl_config
    except (FileNotFoundError, RuntimeError) as e:
        click.secho(f"❌ Cannot read pyproject.toml: {e}", fg="red")
        raise SystemExit(1)

    uvl_file = uvl_cfg.get("file")
    if not uvl_file:
        click.secho("❌ No UVL file configured.", fg="red")
        raise SystemExit(1)

    uvl_path = os.path.join(product_dir, "uvl", uvl_file)
    if not os.path.isfile(uvl_path):
        click.secho(f"❌ UVL file not found: {uvl_path}", fg="red")
        raise SystemExit(1)

    package_map, existing_raw, raw_text = _parse_uvl(uvl_path)
    all_packages = set(package_map.values())
    pkg_to_short = {v: k for k, v in package_map.items()}

    # Parse existing constraints as (src, dst) pairs
    existing_pairs = set()
    for c in existing_raw:
        src, dst = _parse_constraint(c)
        existing_pairs.add((src, dst))

    # Scan code
    feature_paths = _resolve_feature_paths(workspace, product)

    missing: list[tuple[str, str, str]] = []  # (importer_short, imported_short, type)

    for pkg_name in sorted(all_packages):
        short = pkg_to_short.get(pkg_name, pkg_name)
        fpath = feature_paths.get(pkg_name)
        if not fpath:
            continue

        actual_imports = _scan_imports(fpath, pkg_name, all_packages)
        for imp in sorted(actual_imports):
            imp_short = pkg_to_short.get(imp, imp)

            # Check if constraint exists: short => imp_short
            if (short, imp_short) in existing_pairs:
                continue  # already declared

            # Check if reverse exists (inverted)
            if (imp_short, short) in existing_pairs:
                missing.append((short, imp_short, "inverted"))
            else:
                missing.append((short, imp_short, "undeclared"))

    if not missing:
        click.echo()
        click.secho(
            "  ✅ All cross-feature imports have matching UVL constraints.", fg="green"
        )
        click.echo()
        return

    # Interactive wizard
    click.echo()
    click.secho(
        f"  Found {len(missing)} missing constraint(s):\n", fg="cyan", bold=True
    )

    to_add: list[str] = []
    inversions: list[tuple[str, str]] = []

    for importer, imported, issue_type in missing:
        if issue_type == "inverted":
            icon = click.style("⚠ INVERTED", fg="red")
            detail = (
                f"UVL has {imported} => {importer}, but {importer} imports {imported}"
            )
        else:
            icon = click.style("? UNDECLARED", fg="yellow")
            detail = f"{importer} imports {imported}, no UVL constraint exists"

        click.echo(f"  {icon}")
        click.echo(f"    {detail}")
        click.echo()
        click.echo(
            f"    Suggested constraint: {click.style(f'{importer} => {imported}', bold=True)}"
        )
        click.echo()

        choice = click.prompt(
            "    Action",
            type=click.Choice(["add", "skip", "invert"]),
            default="add" if issue_type == "undeclared" else "skip",
            show_choices=True,
        )

        if choice == "add":
            to_add.append(f"{importer} => {imported}")
            click.secho(f"    ✔ Will add: {importer} => {imported}", fg="green")
        elif choice == "invert":
            inversions.append((importer, imported))
            click.secho(
                f"    📝 Marked as inversion — needs code refactoring in {importer}",
                fg="yellow",
            )
        else:
            click.secho("    ⏩ Skipped", fg="bright_black")

        click.echo()

    # Write
    if to_add:
        click.echo(click.style("  ─" * 30, fg="bright_black"))
        click.echo()
        click.echo(f"  Adding {len(to_add)} constraint(s) to {uvl_file}:")
        for c in to_add:
            click.echo(f"    + {c}")
        click.echo()

        if click.confirm("  Write to UVL file?", default=True):
            _write_constraints(uvl_path, raw_text, to_add)
            click.secho(f"  ✅ UVL updated: {uvl_path}", fg="green")
        else:
            click.secho("  ❎ Cancelled.", fg="yellow")
    else:
        click.echo("  No constraints to add.")

    if inversions:
        click.echo()
        click.secho(
            "  ⚠ Inversions that need code refactoring:", fg="yellow", bold=True
        )
        for importer, imported in inversions:
            click.echo(f"    - {importer} should NOT import from {imported}")
            click.echo(
                f"      → Move the dependency to {imported}'s side, or remove the import"
            )
        click.echo()

    click.echo()


cli_command = uvl_fix
