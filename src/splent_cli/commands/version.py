import os
import json
import hashlib
import click
import importlib.metadata

import tomllib
from splent_cli.services import context
from splent_cli.utils.feature_utils import read_features_from_data


def _pkg_version(name: str) -> str | None:
    if not name:
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _pyproject_version(pyproject_path: str) -> str | None:
    """Read version directly from a pyproject.toml — always up to date."""
    if not os.path.exists(pyproject_path):
        return None
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version")
    except Exception:
        return None


def _workspace_pkg_version(workspace: str, pkg_name: str) -> str | None:
    """
    Read version from the package's pyproject.toml in the workspace (source of truth
    for editable installs). Falls back to importlib.metadata if not found.
    """
    pyproject_path = os.path.join(workspace, pkg_name, "pyproject.toml")
    return _pyproject_version(pyproject_path) or _pkg_version(pkg_name)


def _product_version(app_path: str) -> str | None:
    pyproject_path = os.path.join(app_path, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        return None
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version")
    except Exception:
        return None


def _declared_features(app_path: str) -> list:
    pyproject_path = os.path.join(app_path, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        return []
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return read_features_from_data(data)
    except Exception:
        return []


def _feature_location(workspace: str, ref: str) -> str:
    """Return 'cache', 'workspace', or 'missing'."""
    if "/" in ref:
        ns, rest = ref.split("/", 1)
    else:
        ns, rest = "splent_io", ref
    ns_fs = ns.replace("-", "_")
    if "@" in rest:
        name, version = rest.split("@", 1)
        path = os.path.join(
            workspace, ".splent_cache", "features", ns_fs, f"{name}@{version}"
        )
        return "cache" if os.path.isdir(path) else "missing"
    else:
        # Editable: check workspace root first, then cache
        if os.path.isdir(os.path.join(workspace, rest)):
            return "workspace"
        if os.path.isdir(os.path.join(workspace, ".splent_cache", "features", ns_fs, rest)):
            return "cache"
        return "missing"


def _fingerprint(parts: list) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _parse_feature_ref(ref: str) -> tuple:
    """Returns (display_name, version_or_None)."""
    base = ref.split("/", 1)[1] if "/" in ref else ref
    if "@" in base:
        name, version = base.split("@", 1)
        return name, version
    return base, None


@click.command("version", short_help="Show full workspace version snapshot")
@click.option("--json", "as_json", is_flag=True, help="Output in JSON format.")
def version(as_json: bool) -> None:
    """
    Displays a complete version snapshot of the SPLENT workspace:
    CLI, framework, Python, active product, declared features and their cache status.

    A short fingerprint is computed from all versions combined — identical fingerprints
    mean identical workspace states.
    """
    workspace = str(context.workspace())
    app_name = os.getenv("SPLENT_APP")

    cli_v = _workspace_pkg_version(workspace, "splent_cli") or "unknown"
    fw_v = _workspace_pkg_version(workspace, "splent_framework") or "unknown"
    py_v = os.sys.version.split()[0]

    app_v = None
    features = []
    feature_status = []

    if app_name:
        app_path = os.path.join(workspace, app_name)
        app_v = _product_version(app_path)
        features = _declared_features(app_path)
        for ref in features:
            name, ver = _parse_feature_ref(ref)
            location = _feature_location(workspace, ref)
            feature_status.append(
                {
                    "name": name,
                    "version": ver,
                    "location": location,
                    "ref": ref,
                }
            )

    cli_compat = (
        cli_v != "unknown"
        and fw_v != "unknown"
        and cli_v.split(".")[0] == fw_v.split(".")[0]
    )

    fp_parts = [cli_v, fw_v, py_v, app_name or "", app_v or ""]
    for f in feature_status:
        fp_parts.append(f"{f['name']}={f['version'] or 'editable'}")
    fingerprint = _fingerprint(fp_parts)

    if as_json:
        payload = {
            "cli": cli_v,
            "framework": fw_v,
            "python": py_v,
            "compatible": cli_compat,
            "product": {"name": app_name, "version": app_v} if app_name else None,
            "features": feature_status,
            "fingerprint": fingerprint,
        }
        click.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    w = 56
    click.secho("\nSPLENT workspace snapshot", bold=True)
    click.echo("─" * w)

    compat_label = (
        click.style("✔ compatible", fg="green")
        if cli_compat
        else click.style("✖ mismatch", fg="red")
    )
    click.echo(f"  {'CLI':<14} {cli_v}")
    click.echo(f"  {'Framework':<14} {fw_v:<10}  {compat_label}")
    click.echo(f"  {'Python':<14} {py_v}")

    if app_name:
        app_label = f"{app_name}  {app_v or '(version unknown)'}"
        click.echo(f"  {'Product':<14} {app_label}")
    else:
        click.echo(f"  {'Product':<14} " + click.style("(not selected)", fg="yellow"))

    if feature_status:
        click.echo()
        click.echo("  Features declared in pyproject:")
        for i, f in enumerate(feature_status):
            connector = "└──" if i == len(feature_status) - 1 else "├──"
            ver_label = (
                click.style(f"@{f['version']}", fg="cyan")
                if f["version"]
                else click.style("editable", fg="blue")
            )
            loc = f["location"]
            if loc == "cache":
                cache_label = click.style("✔ in cache", fg="green")
            elif loc == "workspace":
                cache_label = click.style("✔ workspace root", fg="magenta")
            else:
                cache_label = click.style("✖ not found", fg="red")
            click.echo(f"  {connector} {f['name']:<32} {ver_label:<18}  {cache_label}")
    elif app_name:
        click.echo()
        click.echo("  " + click.style("No features declared.", fg="yellow"))

    click.echo("─" * w)
    click.echo(f"  Fingerprint  {click.style(fingerprint, fg='cyan', bold=True)}")
    click.echo()
