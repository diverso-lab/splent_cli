import os
import re
import subprocess
import click
import tomllib
import importlib.metadata


@click.command(
    "doctor", short_help="Diagnose workspace consistency and feature cache state"
)
def doctor():
    """
    Performs consistency checks for SPLENT:
    - Environment variables and active app
    - pyproject.toml integrity
    - Installed dependencies
    - Feature cache structure
    - Symlink validation between product and cache
    - Feature pip install status
    - Git dirty state of editable features
    - UVL file presence
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

    if os.getenv("WORKING_DIR"):
        results.append(_ok(f"WORKING_DIR set → {workspace}"))
    else:
        results.append(_warn(f"WORKING_DIR not set, defaulting to {workspace}"))

    env_file = os.path.join(workspace, ".env")
    if os.path.exists(env_file):
        results.append(_ok(".env file found"))
    else:
        results.append(_warn(".env file not found in workspace"))

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

    if os.getenv("GITHUB_TOKEN"):
        results.append(_ok("GITHUB_TOKEN set"))
    else:
        results.append(_warn("GITHUB_TOKEN not set — GitHub releases and private repos will fail"))

    if os.getenv("PYPI_TOKEN") or os.getenv("TWINE_PASSWORD"):
        results.append(_ok("PyPI token set"))
    else:
        results.append(_warn("PYPI_TOKEN / TWINE_PASSWORD not set — PyPI publishing will fail"))

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
        results.append(_warn("No features declared in pyproject.toml"))
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
        # 🐍 PHASE 6: Pip install
        # -------------------------
        click.echo(click.style("\n🐍 Feature pip install", fg="cyan", bold=True))
        results = []
        _check_feature_pip_install(features, results)
        _print_phase(results)
        all_results += results
        total_fail += sum("[✖]" in r for r in results)

        # -------------------------
        # 🌿 PHASE 7: Git dirty
        # -------------------------
        click.echo(click.style("\n🌿 Editable feature git status", fg="cyan", bold=True))
        results = []
        _check_feature_git_dirty(workspace, features, results)
        _print_phase(results)
        all_results += results
        total_fail += sum("[✖]" in r for r in results)

    # -------------------------
    # 🧬 PHASE 8: UVL
    # -------------------------
    click.echo(click.style("\n🧬 UVL", fg="cyan", bold=True))
    results = []
    _check_uvl(app_path, data, results)
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
# Phase helpers
# =====================================================


def _check_features_with_cache(
    workspace: str, features_declared: list, results: list
):
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
        if ver:
            feat_dir = os.path.join(cache_root, org_safe, f"{feat_pkg}@{ver}")
        else:
            feat_dir = os.path.join(cache_root, org_safe, feat_pkg)

        if not os.path.exists(feat_dir):
            label = f"{feat_pkg}@{ver}" if ver else f"{feat_pkg} (editable)"
            results.append(_fail(f"{org_safe}/{label} missing in cache"))
            continue
        src_dir = os.path.join(feat_dir, "src", org_safe, feat_pkg)
        if not os.path.isdir(src_dir):
            label = f"{feat_pkg}@{ver}" if ver else feat_pkg
            results.append(_fail(f"{org_safe}/{label} missing package structure"))
            continue
        pyproject = os.path.join(feat_dir, "pyproject.toml")
        if not os.path.exists(pyproject):
            label = f"{feat_pkg}@{ver}" if ver else feat_pkg
            results.append(_warn(f"{org_safe}/{label} missing pyproject.toml"))
            continue
        label = f"{feat_pkg}@{ver}" if ver else f"{feat_pkg} (editable)"
        results.append(_ok(f"{org_safe}/{label} OK in cache"))


def _check_feature_symlinks(
    workspace: str, app_name: str, features: list, results: list
):
    product_features_dir = os.path.join(workspace, app_name, "features")
    if not os.path.isdir(product_features_dir):
        results.append(_fail(f"No 'features/' directory found in {app_name}"))
        return

    for f in features:
        if "@" not in f:
            continue
        org, rest = f.split("/", 1) if "/" in f else ("splent_io", f)
        feat_pkg, _, ver = rest.partition("@")
        namespace_safe = org.replace("-", "_").replace(".", "_")
        link_path = os.path.join(
            product_features_dir, namespace_safe, f"{feat_pkg}@{ver}"
        )
        target_path = os.path.join(
            workspace, ".splent_cache", "features", namespace_safe, f"{feat_pkg}@{ver}"
        )

        if not os.path.exists(link_path):
            results.append(
                _fail(f"Missing symlink: {app_name}/features/{namespace_safe}/{feat_pkg}@{ver}")
            )
        elif not os.path.islink(link_path):
            results.append(_fail(f"Expected symlink but found directory/file: {link_path}"))
        elif not os.path.exists(target_path):
            results.append(_fail(f"Broken symlink → target missing: {target_path}"))
        else:
            results.append(_ok(f"Symlink OK: {namespace_safe}/{feat_pkg}@{ver}"))


def _check_feature_pip_install(features: list, results: list):
    """Check that editable features are installed as pip packages."""
    editable = []
    for f in features:
        ref = f.split("/", 1)[1] if "/" in f else f
        if "@" not in ref:
            editable.append(ref)

    if not editable:
        results.append(_ok("No editable features to check"))
        return

    for pkg in editable:
        if _pkg_version(pkg):
            results.append(_ok(f"{pkg} installed"))
        else:
            results.append(_fail(f"{pkg} not installed — run: pip install -e .splent_cache/features/.../{pkg}"))


def _check_feature_git_dirty(workspace: str, features: list, results: list):
    """Warn if editable features in cache have uncommitted changes."""
    cache_root = os.path.join(workspace, ".splent_cache", "features")
    checked = False

    for f in features:
        if "/" in f:
            org, rest = f.split("/", 1)
        else:
            org, rest = "splent_io", f

        if "@" in rest:
            continue  # versioned snapshot — skip

        org_fs = org.replace("-", "_")
        feat_dir = os.path.join(cache_root, org_fs, rest)

        if not os.path.isdir(feat_dir):
            continue

        checked = True
        try:
            r = subprocess.run(
                ["git", "-C", feat_dir, "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode != 0:
                results.append(_warn(f"{org_fs}/{rest} — not a git repo or git error"))
            elif r.stdout.strip():
                results.append(_warn(f"{org_fs}/{rest} has uncommitted changes"))
            else:
                results.append(_ok(f"{org_fs}/{rest} clean"))
        except (subprocess.TimeoutExpired, FileNotFoundError):
            results.append(_warn(f"{org_fs}/{rest} — could not run git status"))

    if not checked:
        results.append(_ok("No editable features in cache to check"))


def _check_uvl(app_path: str, data: dict, results: list):
    """Check UVL configuration and file existence."""
    uvl_config = data.get("tool", {}).get("splent", {}).get("uvl")
    if not uvl_config:
        results.append(_ok("No UVL configuration declared"))
        return

    uvl_file = uvl_config.get("file")
    if not uvl_file:
        results.append(_warn("UVL config present but 'file' key missing"))
        return

    uvl_path = os.path.join(app_path, uvl_file)
    uvl_path_subdir = os.path.join(app_path, "uvl", uvl_file)
    if os.path.exists(uvl_path):
        results.append(_ok(f"UVL file found: {uvl_file}"))
    elif os.path.exists(uvl_path_subdir):
        results.append(_ok(f"UVL file found: uvl/{uvl_file}"))
    else:
        results.append(_fail(f"UVL file not found: {uvl_file} (checked root and uvl/)"))

    for key in ("mirror", "doi"):
        if uvl_config.get(key):
            results.append(_ok(f"UVL {key}: {uvl_config[key]}"))
        else:
            results.append(_warn(f"UVL '{key}' not set"))


# =====================================================
# Generic helpers
# =====================================================


def _extract_features(pyproject_path: str, data: dict) -> list:
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


def _find_missing_pkgs(deps: list) -> list:
    missing = []
    for dep in deps:
        # Strip version specifiers: "click==8.1.8" → "click", "flask>=2.0" → "flask"
        pkg = re.split(r"[=<>!~\[]", dep)[0].strip()
        if pkg and not _pkg_version(pkg):
            missing.append(pkg)
    return missing


def _ok(msg: str) -> str:
    return click.style("[✔] ", fg="green") + msg


def _fail(msg: str) -> str:
    return click.style("[✖] ", fg="red") + msg


def _warn(msg: str) -> str:
    return click.style("[⚠] ", fg="yellow") + msg


def _print_phase(results: list):
    for r in results:
        click.echo(r)
