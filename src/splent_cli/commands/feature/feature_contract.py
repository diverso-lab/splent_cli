"""
splent feature:contract

Infer the feature contract from source code at any time during development.

By default it is a dry-run: shows what the contract would look like and
highlights differences from what is currently written in pyproject.toml.

Use --write to actually update [tool.splent.contract] in pyproject.toml
without touching version, git tags, or PyPI.

Works with both editable features (no version) and versioned features (@v1.x.x).
"""

import os
import tomllib
import click
from pathlib import Path

from splent_cli.services import context
from splent_cli.commands.feature.feature_release import infer_contract, write_contract
from splent_cli.utils.feature_utils import normalize_namespace


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# ─────────────────────────────────────────────────────────────────────────────
# Public API — used by feature:release and others
# ─────────────────────────────────────────────────────────────────────────────


def update_contract(feature_path: str, namespace: str, feature_name: str) -> dict:
    """Infer the contract from source code and write it to pyproject.toml.

    Returns the inferred contract dict. Can be called from any command.
    """
    contract = infer_contract(feature_path, namespace, feature_name)
    pyproject_path = os.path.join(feature_path, "pyproject.toml")
    write_contract(pyproject_path, contract, feature_name)
    return contract


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_feature(feature_ref: str, workspace: str) -> tuple[Path, str, str]:
    """Resolve a feature: workspace root first, then cache."""
    if "/" in feature_ref:
        ns_raw, rest = feature_ref.split("/", 1)
        ns = normalize_namespace(ns_raw)
    else:
        ns = DEFAULT_NAMESPACE
        rest = feature_ref

    name, _, version = rest.partition("@")

    # 1. Workspace root (editable features)
    workspace_path = Path(workspace) / name
    if workspace_path.exists():
        return workspace_path, ns, name

    # 2. Cache (pinned features)
    cache_base = Path(workspace) / ".splent_cache" / "features" / ns

    if version:
        cache_path = cache_base / f"{name}@{version}"
    else:
        cache_path = cache_base / name
        if not cache_path.exists():
            candidates = sorted(cache_base.glob(f"{name}@*"))
            if candidates:
                cache_path = candidates[-1]

    if not cache_path.exists():
        raise SystemExit(
            f"❌ Feature not found: {cache_path}\n"
            f"   Run: splent feature:clone {ns.replace('_', '-')}/{name}"
        )
    return cache_path, ns, name


def _read_current_contract(pyproject_path: Path) -> dict:
    """Read the contract block currently written in pyproject.toml."""
    if not pyproject_path.exists():
        return {}
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    raw = data.get("tool", {}).get("splent", {}).get("contract", {})
    ext = raw.get("extensible", {})
    return {
        "routes": raw.get("provides", {}).get("routes", []),
        "blueprints": raw.get("provides", {}).get("blueprints", []),
        "models": raw.get("provides", {}).get("models", []),
        "commands": raw.get("provides", {}).get("commands", []),
        "hooks": raw.get("provides", {}).get("hooks", []),
        "services": raw.get("provides", {}).get("services", []),
        "signals": raw.get("provides", {}).get("signals", []),
        "translations": raw.get("provides", {}).get("translations", []),
        "docker": raw.get("provides", {}).get("docker", []),
        "requires_features": raw.get("requires", {}).get("features", []),
        "env_vars": raw.get("requires", {}).get("env_vars", []),
        "requires_signals": raw.get("requires", {}).get("signals", []),
        "extensible_services": ext.get("services", []),
        "extensible_templates": ext.get("templates", []),
        "extensible_models": ext.get("models", []),
        "extensible_hooks": ext.get("hooks", []),
        "extensible_routes": ext.get("routes", False),
    }


def _diff_field(label: str, old: list, new: list) -> list[str]:
    """Return lines describing added/removed items in a contract field."""
    lines = []
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    for item in added:
        lines.append(click.style(f"    + {label}: {item}", fg="green"))
    for item in removed:
        lines.append(click.style(f"    - {label}: {item}", fg="red"))
    return lines


def _print_contract(contract: dict, feature_name: str) -> None:
    """Pretty-print an inferred contract dict."""

    def _fmt(items: list) -> str:
        if not items:
            return "[]"
        return "[" + ", ".join(f'"{i}"' for i in items) + "]"

    click.echo()
    click.echo(click.style("  [tool.splent.contract.provides]", fg="bright_black"))
    click.echo(f"  routes     = {_fmt(contract['routes'])}")
    click.echo(f"  blueprints = {_fmt(contract['blueprints'])}")
    click.echo(f"  models     = {_fmt(contract['models'])}")
    click.echo(f"  commands   = {_fmt(contract['commands'])}")
    click.echo(f"  hooks      = {_fmt(contract['hooks'])}")
    click.echo(f"  services   = {_fmt(contract['services'])}")
    click.echo(f"  signals    = {_fmt(contract.get('signals', []))}")
    click.echo(f"  translations = {_fmt(contract.get('translations', []))}")
    click.echo(f"  docker     = {_fmt(contract['docker'])}")
    click.echo()
    click.echo(click.style("  [tool.splent.contract.requires]", fg="bright_black"))
    click.echo(f"  features   = {_fmt(contract['requires_features'])}")
    click.echo(f"  env_vars   = {_fmt(contract['env_vars'])}")
    click.echo(f"  signals    = {_fmt(contract.get('requires_signals', []))}")
    click.echo()
    click.echo(click.style("  [tool.splent.contract.extensible]", fg="bright_black"))
    click.echo(f"  services   = {_fmt(contract.get('extensible_services', []))}")
    click.echo(f"  templates  = {_fmt(contract.get('extensible_templates', []))}")
    click.echo(f"  models     = {_fmt(contract.get('extensible_models', []))}")
    click.echo(f"  hooks      = {_fmt(contract.get('extensible_hooks', []))}")
    click.echo(f"  routes     = {contract.get('extensible_routes', False)}")
    click.echo()


def _print_diff(current: dict, inferred: dict) -> bool:
    """
    Print a diff between current and inferred contract.
    Returns True if there are any changes.
    """
    FIELDS = [
        ("routes", "routes"),
        ("blueprints", "blueprints"),
        ("models", "models"),
        ("commands", "commands"),
        ("hooks", "hooks"),
        ("services", "services"),
        ("docker", "docker"),
        ("requires_features", "requires.features"),
        ("env_vars", "requires.env_vars"),
        ("signals", "provides.signals"),
        ("translations", "provides.translations"),
        ("requires_signals", "requires.signals"),
    ]

    diff_lines = []
    for key, label in FIELDS:
        diff_lines.extend(
            _diff_field(label, current.get(key, []), inferred.get(key, []))
        )

    if not diff_lines:
        return False

    click.echo()
    click.echo(click.style("  Changes detected:", bold=True))
    for line in diff_lines:
        click.echo(f"  {line}")
    click.echo()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Command
# ─────────────────────────────────────────────────────────────────────────────


@click.command(
    "feature:contract",
    short_help="Infer and optionally update the feature contract from source code.",
)
@click.argument("feature_ref")
@click.option(
    "--write",
    is_flag=True,
    help="Write the inferred contract to pyproject.toml (default: dry-run only).",
)
@context.requires_product
def feature_contract(feature_ref, write):
    """
    Infer the feature contract from source code at any time during development.

    \b
    By default this is a dry-run: it shows the inferred contract and highlights
    any differences from what is currently written in pyproject.toml.

    \b
    Use --write to update [tool.splent.contract] in pyproject.toml without
    touching version, git tags, or publishing anything.

    \b
    Examples:
      splent feature:contract splent_feature_notes
      splent feature:contract splent_feature_notes --write
      splent feature:contract splent-io/splent_feature_notes --write
    """
    workspace = str(context.workspace())
    cache_path, ns, name = _resolve_feature(feature_ref, workspace)

    pyproject_path = cache_path / "pyproject.toml"

    click.echo()
    click.echo(click.style(f"  feature:contract — {name}", bold=True))
    click.echo(click.style(f"  {'─' * 60}", fg="bright_black"))

    # Infer contract from source
    click.echo("  🔍 Scanning source code…")
    inferred = infer_contract(str(cache_path), ns, name)

    # Show inferred contract
    click.echo(click.style("  Inferred contract:", bold=True))
    _print_contract(inferred, name)

    # Compare with current
    current = _read_current_contract(pyproject_path)
    has_changes = _print_diff(current, inferred)

    if not has_changes:
        click.secho("  ✅ Contract is already up to date.", fg="green")
        click.echo()
        return

    if not write:
        click.echo(
            click.style("  Dry-run — ", fg="yellow")
            + "run with "
            + click.style("--write", bold=True)
            + " to update pyproject.toml."
        )
        click.echo()
        return

    # Write
    update_contract(str(cache_path), ns, name)
    click.secho("  ✅ Contract written to pyproject.toml.", fg="green")

    # Check if config.py needs updating
    _check_config_py(cache_path, ns, name, inferred)

    click.echo()


def _check_config_py(feature_path, ns, name, inferred):
    """Warn if config.py is missing or stale relative to the inferred contract."""
    import re

    env_vars = inferred.get("env_vars", [])
    if not env_vars:
        return

    ns_safe = normalize_namespace(ns)
    config_path = feature_path / "src" / ns_safe / name / "config.py"

    if not config_path.exists():
        click.echo()
        click.secho(
            f"  ⚠  config.py not found — {len(env_vars)} env var(s) detected but not injected into app.config.",
            fg="yellow",
        )
        if click.confirm(
            "     Run feature:inject-config to generate it?", default=True
        ):
            from splent_cli.commands.feature.feature_inject_config import (
                feature_inject_config,
            )

            ctx = click.get_current_context()
            ctx.invoke(feature_inject_config, feature_ref=name, dry_run=False)
        return

    # Config exists — check for missing vars
    text = config_path.read_text()
    existing = set(re.findall(r'"([A-Z][A-Z0-9_]+)":\s*', text))
    missing = sorted(set(env_vars) - existing)

    if missing:
        click.echo()
        click.secho(
            f"  ⚠  config.py is missing {len(missing)} env var(s): {', '.join(missing)}",
            fg="yellow",
        )
        if click.confirm("     Run feature:inject-config to update it?", default=True):
            from splent_cli.commands.feature.feature_inject_config import (
                feature_inject_config,
            )

            ctx = click.get_current_context()
            ctx.invoke(feature_inject_config, feature_ref=name, dry_run=False)


cli_command = feature_contract
