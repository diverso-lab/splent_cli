"""
check:deps — Validate that feature code imports respect UVL dependency constraints.

Cross-checks:
  - UVL constraints (A => B means A depends on B, so A may import from B)
  - Actual Python imports in each feature's source code

Detects:
  - Inverted dependencies (auth importing from profile when UVL says profile => auth)
  - Undeclared dependencies (feature importing another without UVL constraint)
"""

import os
import re

import click

from splent_cli.services import context
from splent_framework.utils.pyproject_reader import PyprojectReader


# ---------------------------------------------------------------------------
# UVL parser — extract dependency graph
# ---------------------------------------------------------------------------


def _parse_uvl_deps(uvl_path: str) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Parse UVL file and return (package_map, allowed_deps).

    package_map: {short_name: package_name}  e.g. {"auth": "splent_feature_auth"}
    allowed_deps: {package: {packages it may import from}}
        If UVL says "profile => auth", profile is allowed to import auth.
    """
    with open(uvl_path, "r", encoding="utf-8") as f:
        text = f.read()

    package_map: dict[str, str] = {}
    constraints: list[tuple[str, str]] = []

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
                short = m.group(1)
                attrs = m.group(2)
                pkg_m = re.search(r"package\s+'([^']+)'", attrs)
                if pkg_m:
                    package_map[short] = pkg_m.group(1)

        if in_constraints and "=>" in stripped:
            parts = stripped.split("=>")
            if len(parts) == 2:
                src = parts[0].split("#")[0].strip()
                dst = parts[1].split("#")[0].strip()
                # src => dst means src depends on dst
                constraints.append((src, dst))

    # Build allowed_deps: for each feature, which features it's allowed to import
    allowed: dict[str, set[str]] = {pkg: set() for pkg in package_map.values()}
    for src_short, dst_short in constraints:
        src_pkg = package_map.get(src_short)
        dst_pkg = package_map.get(dst_short)
        if src_pkg and dst_pkg:
            allowed.setdefault(src_pkg, set()).add(dst_pkg)

    return package_map, allowed


# ---------------------------------------------------------------------------
# Source code scanner — find actual imports of other features
# ---------------------------------------------------------------------------


def _scan_feature_imports(
    feature_path: str, feature_name: str, all_packages: set[str]
) -> tuple[set[str], set[str]]:
    """Scan a feature's source code and templates for cross-feature dependencies.

    Returns (python_imports, template_deps) — both sets of package names.
    """
    python_imports: set[str] = set()
    template_deps: set[str] = set()

    src_dir = None
    for org_dir in os.listdir(os.path.join(feature_path, "src")):
        candidate = os.path.join(feature_path, "src", org_dir, feature_name)
        if os.path.isdir(candidate):
            src_dir = candidate
            break

    if not src_dir:
        return python_imports, template_deps

    # Map blueprint names to package names for template dependency detection
    # Convention: blueprint name is the feature short name (e.g. "auth" for splent_feature_auth)
    bp_to_pkg = {}
    for pkg in all_packages:
        short = pkg.replace("splent_feature_", "")
        bp_to_pkg[short] = pkg

    for root, _, files in os.walk(src_dir):
        for f in files:
            filepath = os.path.join(root, f)

            if f.endswith(".py"):
                try:
                    with open(filepath, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except (OSError, PermissionError):
                    continue

                for match in re.findall(
                    r"(?:from|import)\s+splent_io\.(splent_feature_\w+)", content
                ):
                    if match != feature_name and match in all_packages:
                        python_imports.add(match)

            elif f.endswith(".html"):
                try:
                    with open(filepath, "r", encoding="utf-8") as fh:
                        content = fh.read()
                except (OSError, PermissionError):
                    continue

                # Detect url_for('blueprint.endpoint', ...) references to other features
                for bp_name in re.findall(r"url_for\s*\(\s*['\"](\w+)\.", content):
                    pkg = bp_to_pkg.get(bp_name)
                    if pkg and pkg != feature_name:
                        template_deps.add(pkg)

    return python_imports, template_deps


# ---------------------------------------------------------------------------
# Feature path resolver
# ---------------------------------------------------------------------------


def _resolve_feature_paths(
    workspace: str, product: str, features: list[str]
) -> dict[str, str]:
    """Return {package_name: feature_path} for each declared feature."""
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
# Command
# ---------------------------------------------------------------------------


@click.command(
    "check:deps",
    short_help="Validate that feature imports respect UVL dependency constraints.",
)
def check_deps():
    """
    Cross-check feature source code imports against UVL constraints.

    \b
    For each feature, scans all .py files for imports of other features
    and verifies they are allowed by the UVL dependency model.

    \b
    Detects:
      - Inverted dependencies (importing a feature that depends on you)
      - Undeclared dependencies (importing without a UVL constraint)
    """
    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    # Read UVL (catalog or legacy)
    try:
        reader = PyprojectReader.for_product(product_dir)
        # 1. Catalog: [tool.splent].spl
        spl_name = reader.splent_config.get("spl")
        if spl_name:
            uvl_path = os.path.join(
                workspace, "splent_catalog", spl_name, f"{spl_name}.uvl"
            )
        else:
            # 2. Legacy: [tool.splent.uvl].file
            uvl_file = reader.uvl_config.get("file")
            if not uvl_file:
                click.secho(
                    "  [✖] No UVL configured. Set [tool.splent].spl or [tool.splent.uvl].file.",
                    fg="red",
                )
                raise SystemExit(1)
            uvl_path = os.path.join(product_dir, "uvl", uvl_file)
    except (FileNotFoundError, RuntimeError) as e:
        click.secho(f"  [✖] Cannot read pyproject.toml: {e}", fg="red")
        raise SystemExit(1)

    if not os.path.isfile(uvl_path):
        click.secho(f"  [✖] UVL file not found: {uvl_path}", fg="red")
        raise SystemExit(1)

    package_map, allowed_deps = _parse_uvl_deps(uvl_path)
    all_packages = set(package_map.values())

    # Reverse map: package → short name (for display)
    pkg_to_short = {v: k for k, v in package_map.items()}

    # Resolve feature paths
    feature_paths = _resolve_feature_paths(workspace, product, [])

    click.echo()

    violations = 0
    ok = 0

    for pkg_name in sorted(all_packages):
        short = pkg_to_short.get(pkg_name, pkg_name)
        fpath = feature_paths.get(pkg_name)

        if not fpath:
            click.echo(
                click.style(f"  {short}", bold=True)
                + click.style("  (not in cache, skipped)", fg="bright_black")
            )
            continue

        py_imports, tpl_deps = _scan_feature_imports(fpath, pkg_name, all_packages)
        allowed = allowed_deps.get(pkg_name, set())
        all_deps = py_imports | tpl_deps

        if not all_deps:
            click.echo(
                click.style("  [✔] ", fg="green")
                + click.style(f"{short}", bold=True)
                + " — no cross-feature dependencies"
            )
            ok += 1
            continue

        for imp in sorted(all_deps):
            imp_short = pkg_to_short.get(imp, imp)
            source = "imports" if imp in py_imports else "references (template)"

            if imp in allowed:
                click.echo(
                    click.style("  [✔] ", fg="green")
                    + click.style(f"{short}", bold=True)
                    + f" {source} {imp_short}"
                    + click.style(
                        f"  (allowed: {short} => {imp_short})", fg="bright_black"
                    )
                )
                ok += 1
            else:
                reverse_allowed = allowed_deps.get(imp, set())
                if pkg_name in reverse_allowed:
                    click.echo(
                        click.style("  [✖] ", fg="red")
                        + click.style(f"{short}", bold=True)
                        + f" {source} {imp_short}"
                        + click.style(
                            f"  INVERTED — UVL says {imp_short} => {short}, not the reverse",
                            fg="red",
                        )
                    )
                else:
                    click.echo(
                        click.style("  [✖] ", fg="red")
                        + click.style(f"{short}", bold=True)
                        + f" {source} {imp_short}"
                        + click.style(
                            f"  UNDECLARED — no UVL constraint between {short} and {imp_short}",
                            fg="red",
                        )
                    )
                violations += 1

    click.echo()
    if violations:
        click.secho(
            f"  {violations} violation(s) found. Fix the code or update the UVL.",
            fg="red",
        )
        raise SystemExit(1)
    else:
        click.secho(
            f"  ✅ All cross-feature imports are consistent with UVL ({ok} checks passed).",
            fg="green",
        )
    click.echo()


cli_command = check_deps
