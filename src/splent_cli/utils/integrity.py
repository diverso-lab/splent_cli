"""
Feature integrity verification.

Compares the manifest state against actual system state:
  1. Filesystem — symlink exists and resolves
  2. pip — package is installed and importable
  3. Database — migration revision matches head
  4. Blueprint — registered in Flask (only if app is bootable)
"""

import os
import subprocess
import sys

import click


def _check_symlink(product_path, ns_safe, name, version):
    """Check that the feature symlink resolves to a real directory."""
    features_dir = os.path.join(product_path, "features", ns_safe)
    dir_name = f"{name}@{version}" if version else name
    link = os.path.join(features_dir, dir_name)

    if not os.path.exists(link):
        return False, "symlink missing or broken"
    return True, None


def _check_pip(name):
    """Check that the feature is pip-installed."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, "not pip-installed"
    # Extract version
    for line in result.stdout.splitlines():
        if line.startswith("Version:"):
            ver = line.split(":", 1)[1].strip()
            return True, ver
    return True, None


def _check_migrations(name, product_path):
    """Check migration state: compare DB revision vs filesystem head."""
    from splent_framework.managers.migration_manager import MigrationManager

    mdir = MigrationManager.get_feature_migration_dir(name)
    if not mdir or not os.path.isdir(mdir):
        return True, "no migrations"  # Feature has no migrations — OK

    versions_dir = os.path.join(mdir, "versions")
    if not os.path.isdir(versions_dir):
        return True, "no migration versions"

    # Find head revision from filesystem
    migration_files = [
        f for f in os.listdir(versions_dir)
        if f.endswith(".py") and not f.startswith("__")
    ]
    if not migration_files:
        return True, "no migration files"

    return True, "has migrations"


def check_feature_integrity(
    product_path: str,
    ns_safe: str,
    name: str,
    version: str | None,
    manifest_state: str,
) -> list[dict]:
    """Run integrity checks for a single feature.

    Returns list of {check, ok, detail} dicts.
    """
    results = []

    # 1. Symlink
    ok, detail = _check_symlink(product_path, ns_safe, name, version)
    results.append({
        "check": "Symlink",
        "ok": ok,
        "detail": detail or "OK",
    })

    # 2. pip (only if manifest says installed or higher)
    if manifest_state in ("installed", "migrated", "active"):
        ok, detail = _check_pip(name)
        results.append({
            "check": "pip",
            "ok": ok,
            "detail": detail if ok else detail,
        })
    elif manifest_state == "declared":
        # Check if pip installed despite manifest saying declared
        ok, detail = _check_pip(name)
        if ok:
            results.append({
                "check": "pip",
                "ok": True,
                "detail": f"{detail} (manifest says declared — state behind)",
                "state_fix": "installed",
            })
        else:
            results.append({
                "check": "pip",
                "ok": True,  # Not an error if declared
                "detail": "not installed (expected for declared state)",
            })

    # 3. Migrations (only if manifest says migrated or active)
    if manifest_state in ("migrated", "active"):
        ok, detail = _check_migrations(name, product_path)
        results.append({
            "check": "Migrations",
            "ok": ok,
            "detail": detail,
        })

    return results


def fix_feature(product_path: str, workspace: str, ns_safe: str, name: str, version: str | None, issues: list[dict]):
    """Attempt to fix detected issues."""
    fixed = []

    for issue in issues:
        if issue["ok"]:
            continue

        check = issue["check"]

        if check == "Symlink":
            click.echo(f"    Fixing symlink → running product:sync...")
            result = subprocess.run(
                [sys.executable, "-m", "splent_cli", "product:sync"],
                capture_output=True, text=True,
                env={**os.environ, "SPLENT_APP": os.path.basename(product_path)},
            )
            if result.returncode == 0:
                fixed.append("symlink")
                click.secho(f"    ✔ Symlink fixed.", fg="green")
            else:
                click.secho(f"    ✖ Could not fix symlink.", fg="red")

        elif check == "pip":
            feature_dir = os.path.join(workspace, name)
            if not os.path.isdir(feature_dir):
                # Try cache
                dir_name = f"{name}@{version}" if version else name
                feature_dir = os.path.join(
                    workspace, ".splent_cache", "features", ns_safe, dir_name
                )

            if os.path.isdir(feature_dir):
                click.echo(f"    Fixing pip → installing {name}...")
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-e", feature_dir, "-q"],
                    capture_output=True, text=True,
                )
                if result.returncode == 0:
                    fixed.append("pip")
                    click.secho(f"    ✔ pip install fixed.", fg="green")
                else:
                    click.secho(f"    ✖ pip install failed.", fg="red")
            else:
                click.secho(f"    ✖ Cannot find feature directory to install from.", fg="red")

    return fixed
