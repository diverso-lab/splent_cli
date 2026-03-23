import os
import subprocess
from pathlib import Path
import click
from splent_cli.utils.dynamic_imports import get_current_app_config_value
from splent_cli.utils.feature_utils import (
    get_features_from_pyproject,
    get_normalize_feature_name_in_splent_format,
)
from splent_cli.services import context


def _validate_testing_environment():
    env = get_current_app_config_value("TESTING")
    db_url = get_current_app_config_value("SQLALCHEMY_DATABASE_URI")
    if not env:
        raise AssertionError(f"❌ TESTING mode is not active. Current DB: {db_url}")
    if "test" not in db_url.lower():
        raise AssertionError(f"❌ Non-test database in use: {db_url}")


def _pkg_name(ref: str) -> str:
    """Extract bare package name from a pyproject feature ref.

    'splent_io/splent_feature_auth@v1.1.0' -> 'splent_feature_auth'
    'splent_feature_auth@v1.1.0'           -> 'splent_feature_auth'
    'splent_feature_auth'                  -> 'splent_feature_auth'
    """
    name = ref.split("/")[-1]  # strip namespace
    name = name.split("@")[0]  # strip version
    return name


def _find_feature_root(pkg: str, workspace: Path, product: str) -> Path | None:
    """Locate the feature root directory on disk.

    Checks in order:
    1. Product's features/ symlink directory (resolves to cache path).
    2. .splent_cache/features/ directly.
    3. Bare directory in workspace (editable clone at /workspace/pkg/).
    """
    # 1. Product symlinks: {product}/features/{ns}/{name}[@version]
    features_base = workspace / product / "features"
    if features_base.is_dir():
        for ns_dir in features_base.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                if entry.name.split("@")[0] == pkg:
                    return entry.resolve()

    # 2. Cache: .splent_cache/features/{ns}/{name}[@version]
    cache_root = workspace / ".splent_cache" / "features"
    if cache_root.is_dir():
        for ns_dir in cache_root.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                if entry.is_dir() and entry.name.split("@")[0] == pkg:
                    return entry

    # 3. Bare workspace directory (legacy / manual clone)
    bare = workspace / pkg
    if bare.is_dir():
        return bare

    return None


def _get_feature_roots(feature_ref=None) -> list[tuple[str, Path]]:
    workspace = context.workspace()
    splent_app = context.require_app()

    raw_refs = get_features_from_pyproject()
    if not raw_refs:
        click.secho(
            f"⚠️  No features declared in {splent_app}/pyproject.toml.", fg="yellow"
        )
        raise SystemExit(1)

    if feature_ref:
        normalized = get_normalize_feature_name_in_splent_format(feature_ref)
        raw_refs = [r for r in raw_refs if _pkg_name(r) == normalized]
        if not raw_refs:
            click.secho(
                f"❌ Feature '{normalized}' is not declared in this product.", fg="red"
            )
            raise SystemExit(1)

    result = []
    for ref in raw_refs:
        pkg = _pkg_name(ref)
        root = _find_feature_root(pkg, workspace, splent_app)
        if root:
            result.append((pkg, root))
        else:
            click.secho(f"  ⚠️  {pkg} not found on disk — skipping.", fg="yellow")
    return result


def _collect_test_paths(
    feature_roots: list[tuple[str, Path]],
) -> list[tuple[str, Path, Path]]:
    """Returns [(pkg_name, test_dir, src_dir), ...] for features that have a tests/ directory.

    Handles both flat and namespace-package layouts:
      src/{pkg}/tests/             (flat)
      src/{namespace}/{pkg}/tests/ (namespace package, e.g. splent_io/splent_feature_auth)
    """
    result = []
    for pkg, root in feature_roots:
        src = root / "src"
        if not src.is_dir():
            continue
        # Search up to two levels deep: src/*/*/tests or src/*/tests
        found = list(src.glob(f"*/{pkg}/tests")) + list(src.glob(f"{pkg}/tests"))
        for test_dir in found:
            if test_dir.is_dir():
                result.append((pkg, test_dir, src))
                break  # take first match per feature
    return result


def _all_feature_src_dirs(workspace: Path, product: str) -> list[str]:
    """Return src/ paths for every feature in the product's cache, for PYTHONPATH."""
    cache_root = workspace / ".splent_cache" / "features"
    src_dirs = []
    if cache_root.is_dir():
        for ns_dir in cache_root.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                src = entry / "src"
                if src.is_dir():
                    src_dirs.append(str(src))
    return src_dirs


def _run_pytest(
    test_paths: list[tuple[str, Path, Path]], keyword: str | None, verbose: bool
):
    # Build a combined PYTHONPATH with ALL features in the cache so cross-feature
    # imports resolve correctly even when testing a single feature.
    workspace = context.workspace()
    product = os.getenv("SPLENT_APP", "")
    all_src_dirs = os.pathsep.join(_all_feature_src_dirs(workspace, product))

    passed = failed = 0
    for pkg, test_dir, src_dir in test_paths:
        feature_name = pkg

        click.secho(f"\n  ▶  {feature_name}", fg="cyan", bold=True)

        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = all_src_dirs + (os.pathsep + existing if existing else "")

        cmd = [
            "pytest",
            str(test_dir),
            "--rootdir=.",
            "--ignore-glob=*selenium*",
            "-W",
            "ignore::DeprecationWarning",
        ]
        if verbose:
            cmd.append("-v")
        if keyword:
            cmd.extend(["-k", keyword])

        result = subprocess.run(cmd, env=env, cwd=src_dir)
        if result.returncode == 0:
            passed += 1
        else:
            failed += 1

    click.echo()
    if failed == 0:
        click.secho(f"✅ All {passed} feature(s) passed.", fg="green")
    else:
        click.secho(f"❌ {failed} feature(s) failed, {passed} passed.", fg="red")
        raise SystemExit(1)


@click.command(
    "feature:test",
    short_help="Run pytest on one or all features of the active product.",
)
@click.argument("feature_ref", required=False, metavar="FEATURE_REF")
@click.option(
    "-k", "keyword", help="Only run tests matching this keyword (passed to pytest)."
)
@click.option("-v", "verbose", is_flag=True, help="Verbose pytest output.")
def feature_test(feature_ref, keyword, verbose):
    """
    Run the test suite for features declared in the active product.

    \b
    With no arguments, tests all declared features.
    With FEATURE_REF (e.g. splent_feature_auth or auth), tests only that one.

    Examples:
        splent feature:test
        splent feature:test auth
        splent feature:test -k test_login
        splent feature:test auth -v
    """
    _validate_testing_environment()

    feature_roots = _get_feature_roots(feature_ref)
    test_paths = _collect_test_paths(feature_roots)

    if not test_paths:
        click.secho("⚠️  No test directories found.", fg="yellow")
        return

    label = feature_ref or f"{len(test_paths)} feature(s)"
    click.secho(f"\n🧪 Running tests for {label}...", fg="cyan")

    _run_pytest(test_paths, keyword, verbose)


cli_command = feature_test
