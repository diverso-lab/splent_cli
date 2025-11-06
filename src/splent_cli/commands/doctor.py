import os
import re
import click
import tomllib
import importlib.metadata


@click.command("doctor", help="Diagnose SPLENT workspace consistency and feature cache state")
def doctor():
    """Performs consistency checks for SPLENT: environment, dependencies, and cached features."""
    results = []

    # Python version
    py_v = os.sys.version.split()[0]
    results.append(_ok(f"Python {py_v}"))

    # CLI / Framework version check
    cli_v = _pkg_version("splent_cli")
    fw_v = _pkg_version("splent_framework")
    if cli_v and fw_v and cli_v.split(".")[0] == fw_v.split(".")[0]:
        results.append(_ok(f"CLI {cli_v} and Framework {fw_v} compatible"))
    else:
        results.append(_fail(f"Version mismatch: CLI={cli_v} / Framework={fw_v}"))

    # Environment vars
    app_name = os.getenv("SPLENT_APP")
    workspace = os.getenv("WORKING_DIR", "/workspace")

    if not app_name:
        results.append(_fail("No active app (SPLENT_APP not set)"))
        _print_results(results)
        raise SystemExit(2)

    app_path = os.path.join(workspace, app_name)
    if not os.path.exists(app_path):
        results.append(_fail(f"App folder not found: {app_path}"))
        _print_results(results)
        raise SystemExit(2)

    # App structure check
    src_path = os.path.join(app_path, "src")
    if os.path.isdir(src_path):
        results.append(_ok(f"Active app: {app_name} (source at {src_path})"))
    else:
        results.append(_ok(f"Active app: {app_name} (no src/ folder, flat layout)"))

    # pyproject.toml validation
    pyproject_path = os.path.join(app_path, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        results.append(_fail("pyproject.toml missing in app"))
        _print_results(results)
        raise SystemExit(2)

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        results.append(_ok("pyproject.toml parsed successfully"))
    except Exception as e:
        results.append(_fail(f"Invalid pyproject.toml: {e}"))
        _print_results(results)
        raise SystemExit(2)

    # Dependencies
    deps = data.get("project", {}).get("dependencies", [])
    missing = _find_missing_pkgs(deps)
    if missing:
        results.append(_fail(f"Missing dependencies: {', '.join(missing)}"))
    else:
        results.append(_ok("All dependencies satisfied"))

    # Features declared
    features = _extract_features(pyproject_path, data)
    if features:
        _check_features_with_cache(workspace, features, results)
    else:
        results.append(_fail("No features declared in pyproject.toml"))

    _print_results(results)

    if any("[✖]" in r for r in results):
        click.echo()
        click.secho("Some checks failed. Review your SPLENT workspace.", fg="red")
        raise SystemExit(2)


# -----------------------------
# Feature cache verification
# -----------------------------

def _check_features_with_cache(workspace: str, features_declared: list[str], results: list[str]):
    cache_root = os.path.join(workspace, ".splent_cache", "features")
    if not os.path.isdir(cache_root):
        results.append(_fail(".splent_cache/features not found"))
        return

    declared = [f.split("@")[0].strip() for f in features_declared]
    cached = [d for d in os.listdir(cache_root) if d.startswith("splent_feature_")]

    missing = [f for f in declared if f not in cached]
    extra = [c for c in cached if c not in declared]

    for feature in declared:
        if feature in missing:
            results.append(_fail(f"Feature {feature} not found in .splent_cache"))
            continue

        feature_dir = os.path.join(cache_root, feature)
        versions = [
            v for v in os.listdir(feature_dir)
            if os.path.isdir(os.path.join(feature_dir, v))
        ]
        if not versions:
            results.append(_warn(f"Feature {feature} has no versions in cache"))
            continue

        latest = sorted(versions)[-1]
        version_path = os.path.join(feature_dir, latest)
        pyproject = os.path.join(version_path, "pyproject.toml")

        if os.path.exists(pyproject):
            results.append(_ok(f"Feature {feature} {latest} cached"))
        else:
            results.append(_warn(f"Feature {feature} {latest} missing pyproject.toml"))

    if extra:
        results.append(_warn(f"Extra features in cache (not declared): {', '.join(extra)}"))
    else:
        results.append(_ok("No extra features in cache"))


# -----------------------------
# Helper functions
# -----------------------------

def _extract_features(pyproject_path: str, data: dict) -> list[str]:
    """Extracts features reliably from pyproject.toml (even non-standard schemas)."""
    features = data.get("project", {}).get("features")
    if features and isinstance(features, list):
        return features

    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = re.search(r"features\s*=\s*\[(.*?)\]", content, re.DOTALL)
    if match:
        raw = match.group(1)
        lines = re.findall(r'"([^"]+)"', raw)
        return [line.strip() for line in lines]

    return []


def _ok(msg: str) -> str:
    return click.style("[✔] ", fg="green") + msg


def _fail(msg: str) -> str:
    return click.style("[✖] ", fg="red") + msg


def _warn(msg: str) -> str:
    return click.style("[⚠] ", fg="yellow") + msg


def _pkg_version(name: str):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _find_missing_pkgs(deps: list[str]) -> list[str]:
    missing = []
    for dep in deps:
        pkg = dep.split(" ")[0]
        if not _pkg_version(pkg):
            missing.append(pkg)
    return missing


def _print_results(results: list[str]):
    click.echo(click.style("SPLENT Doctor", fg="cyan", bold=True))
    click.echo(click.style("--------------", fg="cyan"))
    for r in results:
        click.echo(r)
