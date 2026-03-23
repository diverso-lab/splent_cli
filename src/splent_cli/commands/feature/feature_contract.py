"""
splent feature:contract

Infer the feature contract from source code at any time during development.

By default it is a dry-run: shows what the contract would look like and
highlights differences from what is currently written in pyproject.toml.

Use --write to actually update [tool.splent.contract] in pyproject.toml
without touching version, git tags, or PyPI.
"""

import os
import tomllib
import click
from pathlib import Path

from splent_cli.services import context
from splent_cli.commands.feature.feature_release import infer_contract, write_contract


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_editable(feature_ref: str, workspace: str) -> tuple[Path, str, str]:
    """Resolve an editable (non-versioned) feature from the cache."""
    base, _, version = feature_ref.partition("@")
    if version:
        raise SystemExit(
            f"❌ feature:contract only works on editable features.\n"
            f"   Remove the version suffix: {base}"
        )

    if "/" in base:
        ns_raw, name = base.split("/", 1)
        ns = ns_raw.replace("-", "_")
    else:
        ns = DEFAULT_NAMESPACE
        name = base

    cache_path = Path(workspace) / ".splent_cache" / "features" / ns / name
    if not cache_path.exists():
        raise SystemExit(
            f"❌ Editable feature not found: {cache_path}\n"
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
    return {
        "routes":     raw.get("provides", {}).get("routes", []),
        "blueprints": raw.get("provides", {}).get("blueprints", []),
        "models":     raw.get("provides", {}).get("models", []),
        "commands":   raw.get("provides", {}).get("commands", []),
        "hooks":      raw.get("provides", {}).get("hooks", []),
        "services":   raw.get("provides", {}).get("services", []),
        "docker":     raw.get("provides", {}).get("docker", []),
        "requires_features": raw.get("requires", {}).get("features", []),
        "env_vars":          raw.get("requires", {}).get("env_vars", []),
    }


def _diff_field(label: str, old: list, new: list) -> list[str]:
    """Return lines describing added/removed items in a contract field."""
    lines = []
    added   = sorted(set(new) - set(old))
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
    click.echo(f"  docker     = {_fmt(contract['docker'])}")
    click.echo()
    click.echo(click.style("  [tool.splent.contract.requires]", fg="bright_black"))
    click.echo(f"  features   = {_fmt(contract['requires_features'])}")
    click.echo(f"  env_vars   = {_fmt(contract['env_vars'])}")
    click.echo()


def _print_diff(current: dict, inferred: dict) -> bool:
    """
    Print a diff between current and inferred contract.
    Returns True if there are any changes.
    """
    FIELDS = [
        ("routes",            "routes"),
        ("blueprints",        "blueprints"),
        ("models",            "models"),
        ("commands",          "commands"),
        ("hooks",             "hooks"),
        ("services",          "services"),
        ("docker",            "docker"),
        ("requires_features", "requires.features"),
        ("env_vars",          "requires.env_vars"),
    ]

    diff_lines = []
    for key, label in FIELDS:
        diff_lines.extend(_diff_field(label, current.get(key, []), inferred.get(key, [])))

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
    cache_path, ns, name = _resolve_editable(feature_ref, workspace)

    pyproject_path = cache_path / "pyproject.toml"

    click.echo()
    click.echo(click.style(f"  feature:contract — {name}", bold=True))
    click.echo(click.style(f"  {'─' * 60}", fg="bright_black"))

    # Infer contract from source
    click.echo(f"  🔍 Scanning source code…")
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
    write_contract(str(pyproject_path), inferred, name)
    click.secho("  ✅ Contract written to pyproject.toml.", fg="green")
    click.echo()


cli_command = feature_contract
