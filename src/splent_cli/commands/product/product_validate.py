"""
product:validate — Validate the product: configuration, compatibility, and dependencies.

Three phases:
  1. Configuration (UVL) — is the feature selection satisfiable?
  2. Compatibility (contracts) — are there route/blueprint/model/service collisions?
  3. Dependencies (imports) — do feature imports respect UVL constraints?
"""

import os
import sys

import click

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    normalize_feature_name as _normalize_feature_name,
    resolve_uvl_path as _resolve_uvl_path,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    write_csvconf_full as _write_csvconf_full,
)


# ── Phase 1: UVL SAT ────────────────────────────────────────────────


def _infer_parents(uvl_path: str, selected: set[str]) -> set[str]:
    """Activate parent features for selected children in the UVL tree."""
    from splent_cli.commands.uvl.uvl_utils import (
        get_root_feature,
        iter_children,
        _discover_metamodels,
    )

    dm = _discover_metamodels()
    fm = dm.use_transformation_t2m(uvl_path, "fm")
    root = get_root_feature(fm)

    parent_map = {}
    stack = [root]
    while stack:
        node = stack.pop()
        name = getattr(node, "name", None)
        for child in iter_children(node):
            child_name = getattr(child, "name", None)
            if child_name and name:
                parent_map[child_name] = name
            stack.append(child)

    extra = set()
    for feat in selected:
        current = feat
        while current in parent_map:
            parent = parent_map[current]
            if parent in selected or parent in extra:
                break
            extra.add(parent)
            current = parent

    return extra


def _run_sat_check(workspace, app_name, data, feature_list, print_config):
    """Run UVL SAT check. Returns (ok, selected, universe, uvl_path)."""
    local_uvl = _resolve_uvl_path(workspace, app_name, data)
    universe, root_name = _list_all_features_from_uvl(local_uvl)

    if feature_list:
        selected = set(f.strip() for f in feature_list.split(",") if f.strip())
        selected.add(root_name)
    else:
        from splent_cli.utils.feature_utils import read_features_from_data

        env = os.getenv("SPLENT_ENV", "dev")
        deps = read_features_from_data(data, env)
        selected = {_normalize_feature_name(d) for d in deps}
        selected.add(root_name)

    unknown = sorted([f for f in selected if f not in universe])
    if unknown:
        raise click.ClickException(
            f"Unknown feature(s) not in UVL: {', '.join(unknown)}"
        )

    selected |= _infer_parents(local_uvl, selected)
    conf_path = _write_csvconf_full(universe, selected)

    try:
        from splent_cli.commands.uvl.uvl_utils import _require_flamapy

        _require_flamapy()
        from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel

        fm = FLAMAFeatureModel(local_uvl)
        ok = fm.satisfiable_configuration(conf_path, full_configuration=False)
    finally:
        try:
            os.remove(conf_path)
        except OSError:
            pass

    return ok, selected, universe, local_uvl


# ── Phase 2: Contract compatibility ──────────────────────────────────


def _run_compat_check(workspace, product_dir):
    """Run contract compatibility check. Returns (findings, errors, warnings)."""
    from splent_cli.commands.feature.feature_compat import run_all_product_check

    findings = run_all_product_check(workspace, product_dir)
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    return findings, errors, warnings


# ── Phase 3: Import dependencies ─────────────────────────────────────


def _run_deps_check(workspace, product):
    """Run import dependency check. Returns (violations, ok_count)."""
    from splent_cli.commands.check.check_deps import (
        _parse_uvl_deps,
        _resolve_feature_paths,
        _scan_feature_imports,
    )
    from splent_framework.utils.pyproject_reader import PyprojectReader

    product_dir = os.path.join(workspace, product)

    try:
        reader = PyprojectReader.for_product(product_dir)
        spl_name = reader.splent_config.get("spl")
        if spl_name:
            uvl_path = os.path.join(
                workspace, "splent_catalog", spl_name, f"{spl_name}.uvl"
            )
        else:
            uvl_file = reader.uvl_config.get("file")
            if not uvl_file:
                return [], 0
            uvl_path = os.path.join(product_dir, "uvl", uvl_file)
    except (FileNotFoundError, RuntimeError):
        return [], 0

    if not os.path.isfile(uvl_path):
        return [], 0

    package_map, allowed_deps = _parse_uvl_deps(uvl_path)
    all_packages = set(package_map.values())
    pkg_to_short = {v: k for k, v in package_map.items()}
    feature_paths = _resolve_feature_paths(workspace, product, [])

    violations = []
    ok_count = 0

    for pkg_name in sorted(all_packages):
        short = pkg_to_short.get(pkg_name, pkg_name)
        fpath = feature_paths.get(pkg_name)
        if not fpath:
            continue

        py_imports, tpl_deps = _scan_feature_imports(fpath, pkg_name, all_packages)
        allowed = allowed_deps.get(pkg_name, set())
        all_deps = py_imports | tpl_deps

        if not all_deps:
            ok_count += 1
            continue

        for imp in sorted(all_deps):
            imp_short = pkg_to_short.get(imp, imp)
            if imp in allowed:
                ok_count += 1
            else:
                reverse_allowed = allowed_deps.get(imp, set())
                if pkg_name in reverse_allowed:
                    violations.append(
                        f"{short} imports {imp_short} (INVERTED — UVL says {imp_short} => {short})"
                    )
                else:
                    violations.append(
                        f"{short} imports {imp_short} (UNDECLARED — no UVL constraint)"
                    )

    return violations, ok_count


# ── Command ──────────────────────────────────────────────────────────


@click.command(
    "product:validate",
    short_help="Validate the product: UVL satisfiability, contract compatibility, and imports.",
)
@click.option(
    "--features",
    "feature_list",
    default=None,
    help="Comma-separated feature list to validate (instead of pyproject).",
)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--print-config", is_flag=True, help="Print the generated 0/1 assignment")
@click.option(
    "--only",
    type=click.Choice(["config", "compat", "deps"], case_sensitive=False),
    default=None,
    help="Run only one phase: config (UVL SAT), compat (contracts), or deps (imports).",
)
def product_validate(feature_list, pyproject, print_config, only):
    """
    Validate the active product in three phases:

    \b
    1. Configuration (UVL) — is the feature selection satisfiable?
    2. Compatibility (contracts) — route, blueprint, model, service collisions?
    3. Dependencies (imports) — do imports respect UVL constraints?

    \b
    All three must pass for the product to be valid.

    \b
    Run a single phase with --only:
      splent product:validate --only config
      splent product:validate --only compat
      splent product:validate --only deps

    \b
    Other examples:
      splent product:validate
      splent product:validate --features auth,profile,redis
      splent product:validate --print-config
    """
    context.require_app()
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_dir = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_dir, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    run_config = only is None or only == "config"
    run_compat = only is None or only == "compat"
    run_deps = only is None or only == "deps"

    click.echo()
    click.echo(click.style(f"  Validating {app_name}...", bold=True))
    click.echo()

    failed = False

    # ── Phase 1: Configuration (UVL SAT) ─────────────────────────────
    if run_config:
        click.echo(
            click.style("  1. Configuration", bold=True)
            + click.style("  (UVL — splent product:validate --only config)  ", dim=True)
        )

        try:
            sat_ok, selected, universe, uvl_path = _run_sat_check(
                workspace, app_name, data, feature_list, print_config
            )
        except Exception as e:
            click.secho(f"     ❌ {e}", fg="red")
            sat_ok = False
            selected = set()

        if sat_ok:
            click.secho(
                f"     ✅ Feature selection is satisfiable ({len(selected)} features)",
                fg="green",
            )
        else:
            click.secho(
                "     ❌ Configuration is NOT satisfiable under UVL constraints",
                fg="red",
            )
            failed = True

        if print_config and sat_ok:
            click.echo()
            for feat in sorted(universe):
                click.echo(f"     {feat}={1 if feat in selected else 0}")

        click.echo()

    # ── Phase 2: Compatibility (contracts) ───────────────────────────
    if run_compat:
        click.echo(
            click.style("  2. Compatibility", bold=True)
            + click.style(
                "  (contracts — splent product:validate --only compat)  ", dim=True
            )
        )

        try:
            findings, errors, warnings = _run_compat_check(workspace, product_dir)
        except Exception as e:
            click.secho(f"     ❌ {e}", fg="red")
            errors = [{"message": str(e)}]
            warnings = []
            findings = []

        if errors:
            for err in errors:
                click.secho(
                    f"     ❌ [{err.get('field', '?')}] {err['message']}", fg="red"
                )
            failed = True
        elif warnings:
            for w in warnings:
                click.secho(
                    f"     ⚠️  [{w.get('field', '?')}] {w['message']}", fg="yellow"
                )
            click.secho(
                "     ✅ No errors (warnings above are non-blocking)", fg="green"
            )
        else:
            click.secho("     ✅ No collisions detected", fg="green")

        infos = [f for f in findings if f["severity"] == "info"]
        if infos:
            click.secho(
                f"     ℹ️  {len(infos)} informational note(s)",
                fg="bright_black",
            )

        click.echo()

    # ── Phase 3: Dependencies (imports) ──────────────────────────────
    if run_deps:
        click.echo(
            click.style("  3. Dependencies", bold=True)
            + click.style(
                "  (imports — splent product:validate --only deps)  ", dim=True
            )
        )

        try:
            violations, ok_count = _run_deps_check(workspace, app_name)
        except Exception as e:
            click.secho(f"     ❌ {e}", fg="red")
            violations = [str(e)]
            ok_count = 0

        if violations:
            for v in violations:
                click.secho(f"     ❌ {v}", fg="red")
            failed = True
        else:
            click.secho(
                f"     ✅ All imports respect UVL constraints ({ok_count} checks)",
                fg="green",
            )

        click.echo()

    # ── Verdict ──────────────────────────────────────────────────────
    if failed:
        click.secho("  ❌ Product validation failed.", fg="red", bold=True)
        click.echo()
        sys.exit(2)
    else:
        click.secho("  ✅ Product is valid.", fg="green", bold=True)
        click.echo()


cli_command = product_validate
