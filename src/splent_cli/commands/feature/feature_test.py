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


# Test levels in execution order (default = first three)
TEST_LEVELS = ("unit", "integration", "functional", "e2e", "load")
DEFAULT_LEVELS = ("unit", "integration", "functional")


def _validate_testing_environment():
    env = get_current_app_config_value("TESTING")
    db_url = get_current_app_config_value("SQLALCHEMY_DATABASE_URI")
    if not env:
        raise AssertionError(f"❌ TESTING mode is not active. Current DB: {db_url}")
    if "test" not in db_url.lower():
        raise AssertionError(f"❌ Non-test database in use: {db_url}")


def _pkg_name(ref: str) -> str:
    name = ref.split("/")[-1]
    name = name.split("@")[0]
    return name


def _find_feature_root(pkg: str, workspace: Path, product: str) -> Path | None:
    # 1. Workspace root (editable features)
    bare = workspace / pkg
    if bare.is_dir():
        return bare

    # 2. Product symlinks
    features_base = workspace / product / "features"
    if features_base.is_dir():
        for ns_dir in features_base.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                if entry.name.split("@")[0] == pkg:
                    return entry.resolve()

    # 3. Cache
    cache_root = workspace / ".splent_cache" / "features"
    if cache_root.is_dir():
        for ns_dir in cache_root.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                if entry.is_dir() and entry.name.split("@")[0] == pkg:
                    return entry

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
    result = []
    for pkg, root in feature_roots:
        src = root / "src"
        if not src.is_dir():
            continue
        found = list(src.glob(f"*/{pkg}/tests")) + list(src.glob(f"{pkg}/tests"))
        for test_dir in found:
            if test_dir.is_dir():
                result.append((pkg, test_dir, src))
                break
    return result


def _all_feature_src_dirs(workspace: Path, product: str) -> list[str]:
    """Return src/ paths for every feature (workspace root + cache)."""
    src_dirs = []

    # Workspace root editable features
    for entry in workspace.iterdir():
        if entry.is_dir() and entry.name.startswith("splent_feature_"):
            src = entry / "src"
            if src.is_dir():
                src_dirs.append(str(src))

    # Cache (pinned features)
    cache_root = workspace / ".splent_cache" / "features"
    if cache_root.is_dir():
        for ns_dir in cache_root.iterdir():
            if not ns_dir.is_dir():
                continue
            for entry in ns_dir.iterdir():
                src = entry / "src"
                if src.is_dir():
                    src_dirs.append(str(src))

    return src_dirs


def _resolve_levels(unit, integration, functional, e2e, load) -> tuple[str, ...]:
    """Determine which test levels to run based on CLI flags."""
    explicit = []
    if unit:
        explicit.append("unit")
    if integration:
        explicit.append("integration")
    if functional:
        explicit.append("functional")
    if e2e:
        explicit.append("e2e")
    if load:
        explicit.append("load")
    return tuple(explicit) if explicit else DEFAULT_LEVELS


def _run_pytest(
    test_paths: list[tuple[str, Path, Path]],
    levels: tuple[str, ...],
    keyword: str | None,
    verbose: bool,
):
    workspace = context.workspace()
    product = os.getenv("SPLENT_APP", "")
    all_src_dirs = os.pathsep.join(_all_feature_src_dirs(workspace, product))

    passed = failed = skipped = 0
    for pkg, test_dir, src_dir in test_paths:
        click.secho(f"\n  ▶  {pkg}", fg="cyan", bold=True)

        for level in levels:
            level_dir = test_dir / level
            if not level_dir.is_dir():
                continue

            # Check there are actual test files
            test_files = list(level_dir.glob("test_*.py"))
            if not test_files:
                continue

            env = os.environ.copy()
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = all_src_dirs + (
                os.pathsep + existing if existing else ""
            )

            cmd = [
                "pytest",
                str(level_dir),
                "--rootdir=.",
                "-W",
                "ignore::DeprecationWarning",
            ]
            if verbose:
                cmd.append("-v")
            if keyword:
                cmd.extend(["-k", keyword])

            level_color = {
                "unit": "green",
                "integration": "yellow",
                "functional": "blue",
                "e2e": "magenta",
                "load": "red",
            }.get(level, "white")

            click.echo(f"     {click.style(level, fg=level_color, bold=True)}")

            result = subprocess.run(cmd, env=env, cwd=src_dir)
            if result.returncode == 0:
                passed += 1
            elif result.returncode == 5:
                # pytest exit code 5 = no tests collected (not a failure)
                skipped += 1
            else:
                failed += 1

    click.echo()
    parts = [f"{passed} passed"]
    if skipped:
        parts.append(f"{skipped} skipped")
    if failed:
        parts.append(f"{failed} failed")
    summary = ", ".join(parts)

    if failed == 0:
        click.secho(f"✅ {summary}.", fg="green")
    else:
        click.secho(f"❌ {summary}.", fg="red")
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
@click.option("--unit", is_flag=True, help="Run only unit tests.")
@click.option("--integration", is_flag=True, help="Run only integration tests.")
@click.option("--functional", is_flag=True, help="Run only functional tests.")
@click.option("--e2e", is_flag=True, help="Run only end-to-end (Selenium) tests.")
@click.option("--load", is_flag=True, help="Run only load (Locust) tests.")
def feature_test(
    feature_ref, keyword, verbose, unit, integration, functional, e2e, load
):
    """
    Run the test suite for features declared in the active product.

    \b
    By default, runs unit + integration + functional tests.
    Use flags to select specific levels.

    \b
    Examples:
        splent feature:test                    # all features, default levels
        splent feature:test auth               # only auth feature
        splent feature:test --unit             # only unit tests
        splent feature:test auth --functional  # functional tests for auth
        splent feature:test -k test_login -v   # keyword filter, verbose
    """
    _validate_testing_environment()

    levels = _resolve_levels(unit, integration, functional, e2e, load)

    feature_roots = _get_feature_roots(feature_ref)
    test_paths = _collect_test_paths(feature_roots)

    if not test_paths:
        click.secho("⚠️  No test directories found.", fg="yellow")
        return

    label = feature_ref or f"{len(test_paths)} feature(s)"
    level_labels = ", ".join(levels)
    click.secho(f"\n🧪 Running tests for {label} [{level_labels}]...", fg="cyan")

    _run_pytest(test_paths, levels, keyword, verbose)


cli_command = feature_test
