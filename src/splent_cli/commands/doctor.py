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
    """
    Valida la cache de features con estructura esperada:
      /workspace/.splent_cache/features/<org_safe>/<feature_pkg>@<version>/
        ├─ pyproject.toml
        └─ src/<org_safe>/<feature_pkg>/
    """
    cache_root = os.path.join(workspace, ".splent_cache", "features")
    if not os.path.isdir(cache_root):
        results.append(_fail(".splent_cache/features not found"))
        return

    # Normaliza la lista declarada a [("splent_io", "splent_feature_xxx", "v1.0.0"), ...]
    decl_norm = []
    for f in features_declared:
        # formatos posibles: "splent_io/splent_feature_x@v1.0.0" o "splent_feature_x@v1.0.0"
        if "/" in f:
            org_safe, rest = f.split("/", 1)
        else:
            org_safe, rest = "splent_io", f
        feat_pkg, _, ver = rest.partition("@")
        decl_norm.append((org_safe.replace("-", "_"), feat_pkg, ver or ""))

    # Recorre organizaciones
    for org_safe in os.listdir(cache_root):
        org_dir = os.path.join(cache_root, org_safe)
        if not os.path.isdir(org_dir):
            # ignora ficheros como __init__.py
            continue

        # Dentro deben existir carpetas "splent_feature_xxx@version"
        for entry in os.listdir(org_dir):
            feat_dir = os.path.join(org_dir, entry)
            if not os.path.isdir(feat_dir):
                continue

            # Valida patrón "nombre@version"
            if "@" not in entry:
                results.append(_warn(f"{org_safe}/{entry} no sigue el patrón <feature>@<version>"))
                continue

            feat_pkg, ver = entry.split("@", 1)
            if not feat_pkg.startswith("splent_feature_"):
                results.append(_warn(f"{org_safe}/{entry} no es un paquete de feature válido"))
                continue

            # Estructura esperada
            src_dir = os.path.join(feat_dir, "src")
            expected_ns_dir = os.path.join(src_dir, org_safe)
            expected_pkg_dir = os.path.join(expected_ns_dir, feat_pkg)
            pyproject = os.path.join(feat_dir, "pyproject.toml")

            if not os.path.isdir(src_dir):
                results.append(_fail(f"Feature {org_safe}/{entry} missing folder: {src_dir}"))
                continue
            if not os.path.isdir(expected_ns_dir):
                results.append(_fail(f"Feature {org_safe}/{entry} missing namespace folder: {expected_ns_dir}"))
                continue
            if not os.path.isdir(expected_pkg_dir):
                results.append(_fail(f"Feature {org_safe}/{entry} missing package folder: {expected_pkg_dir}"))
                continue
            if not os.path.exists(pyproject):
                results.append(_warn(f"Feature {org_safe}/{entry} missing pyproject.toml"))
                continue

            results.append(_ok(f"Feature {org_safe}/{entry} structure OK"))

    results.append(_ok("Feature namespace validation complete"))


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
