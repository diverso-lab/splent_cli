import os
import re
import click
import tomllib
import importlib.metadata


@click.command("doctor", help="Diagnose SPLENT workspace consistency and feature cache state")
def doctor():
    """
    Performs consistency checks for SPLENT:
    - Environment variables and active app
    - pyproject.toml integrity
    - Installed dependencies
    - Feature cache structure
    - Symlink validation between product and cache
    """

    workspace = os.getenv("WORKING_DIR", "/workspace")
    app_name = os.getenv("SPLENT_APP")

    click.echo(click.style("\n🩺 SPLENT Doctor\n", fg="cyan", bold=True))
    all_results = []
    total_fail = 0

    # -------------------------
    # 🧱 PHASE 1: Environment
    # -------------------------
    click.echo(click.style("🧱 Environment", fg="cyan", bold=True))
    results = []

    py_v = os.sys.version.split()[0]
    results.append(_ok(f"Python {py_v}"))

    cli_v = _pkg_version("splent_cli")
    fw_v = _pkg_version("splent_framework")
    if cli_v and fw_v and cli_v.split(".")[0] == fw_v.split(".")[0]:
        results.append(_ok(f"CLI {cli_v} and Framework {fw_v} compatible"))
    else:
        results.append(_fail(f"Version mismatch: CLI={cli_v} / Framework={fw_v}"))

    if not app_name:
        results.append(_fail("SPLENT_APP not set"))
    else:
        app_path = os.path.join(workspace, app_name)
        if not os.path.exists(app_path):
            results.append(_fail(f"App folder not found: {app_path}"))
        else:
            src_path = os.path.join(app_path, "src")
            if os.path.isdir(src_path):
                results.append(_ok(f"Active app: {app_name} (with src/)"))
            else:
                results.append(_ok(f"Active app: {app_name} (flat layout)"))    

    _print_phase(results)
    all_results += results
    total_fail += sum("[✖]" in r for r in results)

    # -------------------------
    # 📄 PHASE 2: pyproject.toml
    # -------------------------
    click.echo(click.style("\n📄 pyproject.toml", fg="cyan", bold=True))
    results = []
    if not app_name:
        results.append(_fail("Cannot continue without SPLENT_APP"))
        _print_phase(results)
        raise SystemExit(2)

    app_path = os.path.join(workspace, app_name)
    pyproject_path = os.path.join(app_path, "pyproject.toml")

    if not os.path.exists(pyproject_path):
        results.append(_fail("pyproject.toml missing"))
        _print_phase(results)
        raise SystemExit(2)

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        results.append(_ok("pyproject.toml parsed successfully"))
    except Exception as e:
        results.append(_fail(f"Invalid pyproject.toml: {e}"))
        _print_phase(results)
        raise SystemExit(2)

    _print_phase(results)
    all_results += results

    # -------------------------
    # 📦 PHASE 3: Dependencies
    # -------------------------
    click.echo(click.style("\n📦 Dependencies", fg="cyan", bold=True))
    results = []
    deps = data.get("project", {}).get("dependencies", [])
    missing = _find_missing_pkgs(deps)
    if missing:
        results.append(_fail(f"Missing: {', '.join(missing)}"))
    else:
        results.append(_ok("All dependencies satisfied"))
    _print_phase(results)
    all_results += results
    total_fail += sum("[✖]" in r for r in results)

    # -------------------------
    # 🧩 PHASE 4: Features & Cache
    # -------------------------
    click.echo(click.style("\n🧩 Features & Cache", fg="cyan", bold=True))
    results = []

    features = _extract_features(pyproject_path, data)
    if not features:
        results.append(_fail("No features declared"))
        _print_phase(results)
        all_results += results
    else:
        _check_features_with_cache(workspace, features, results)
        _print_phase(results)
        all_results += results
        total_fail += sum("[✖]" in r for r in results)

        # -------------------------
        # 🔗 PHASE 5: Symlinks
        # -------------------------
        click.echo(click.style("\n🔗 Symlink Validation", fg="cyan", bold=True))
        results = []
        _check_feature_symlinks(workspace, app_name, features, results)
        _print_phase(results)
        all_results += results
        total_fail += sum("[✖]" in r for r in results)

    # -------------------------
    # 🧾 SUMMARY
    # -------------------------
    click.echo(click.style("\n🧾 Summary", fg="cyan", bold=True))
    click.echo(f"✔ OK: {sum('[✔]' in r for r in all_results)}")
    click.echo(f"⚠ Warn: {sum('[⚠]' in r for r in all_results)}")
    click.echo(f"✖ Fail: {sum('[✖]' in r for r in all_results)}")

    if total_fail > 0:
        click.echo()
        click.secho("Some checks failed. Review your SPLENT workspace.", fg="red")
        raise SystemExit(2)
    else:
        click.echo()
        click.secho("✅ All checks passed successfully.", fg="green")


# =====================================================
# Feature cache and symlink validation helpers
# =====================================================

def _check_features_with_cache(workspace: str, features_declared: list[str], results: list[str]):
    cache_root = os.path.join(workspace, ".splent_cache", "features")
    if not os.path.isdir(cache_root):
        results.append(_fail(".splent_cache/features not found"))
        return

    decl_norm = []
    for f in features_declared:
        if "/" in f:
            org_safe, rest = f.split("/", 1)
        else:
            org_safe, rest = "splent_io", f
        feat_pkg, _, ver = rest.partition("@")
        decl_norm.append((org_safe.replace("-", "_"), feat_pkg, ver or ""))

    for org_safe, feat_pkg, ver in decl_norm:
        feat_dir = os.path.join(cache_root, org_safe, f"{feat_pkg}@{ver}")
        if not os.path.exists(feat_dir):
            results.append(_fail(f"{org_safe}/{feat_pkg}@{ver} missing in cache"))
            continue
        src_dir = os.path.join(feat_dir, "src", org_safe, feat_pkg)
        if not os.path.isdir(src_dir):
            results.append(_fail(f"{org_safe}/{feat_pkg}@{ver} missing package structure"))
            continue
        pyproject = os.path.join(feat_dir, "pyproject.toml")
        if not os.path.exists(pyproject):
            results.append(_warn(f"{org_safe}/{feat_pkg}@{ver} missing pyproject.toml"))
            continue
        results.append(_ok(f"{org_safe}/{feat_pkg}@{ver} OK in cache"))


def _check_feature_symlinks(workspace: str, app_name: str, features: list[str], results: list[str]):
    product_features_dir = os.path.join(workspace, app_name, "features")
    if not os.path.isdir(product_features_dir):
        results.append(_fail(f"No 'features/' directory found in {app_name}"))
        return

    for f in features:
        if "@" not in f:
            continue  # local feature
        org, rest = f.split("/", 1) if "/" in f else ("splent_io", f)
        feat_pkg, _, ver = rest.partition("@")
        namespace_safe = org.replace("-", "_").replace(".", "_")
        link_path = os.path.join(product_features_dir, namespace_safe, f"{feat_pkg}@{ver}")
        target_path = os.path.join(workspace, ".splent_cache", "features", namespace_safe, f"{feat_pkg}@{ver}")

        if not os.path.exists(link_path):
            results.append(_fail(f"Missing symlink: {app_name}/features/{namespace_safe}/{feat_pkg}@{ver}"))
        elif not os.path.islink(link_path):
            results.append(_fail(f"Expected symlink but found directory/file: {link_path}"))
        elif not os.path.exists(target_path):
            results.append(_fail(f"Broken symlink → target missing: {target_path}"))
        else:
            results.append(_ok(f"Symlink OK: {namespace_safe}/{feat_pkg}@{ver}"))


# =====================================================
# Generic helpers
# =====================================================

def _extract_features(pyproject_path: str, data: dict) -> list[str]:
    features = data.get("project", {}).get("optional-dependencies", {}).get("features")
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


def _ok(msg: str) -> str:
    return click.style("[✔] ", fg="green") + msg


def _fail(msg: str) -> str:
    return click.style("[✖] ", fg="red") + msg


def _warn(msg: str) -> str:
    return click.style("[⚠] ", fg="yellow") + msg


def _print_phase(results: list[str]):
    for r in results:
        click.echo(r)
