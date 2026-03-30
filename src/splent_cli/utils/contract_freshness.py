"""
Contract freshness detection.

Compares the mtime of a feature's pyproject.toml against its source files.
If any .py or .html file is newer than the pyproject, the contract may be stale.
"""

import os

import click


def _pyproject_mtime(feature_dir: str) -> float | None:
    """Return mtime of pyproject.toml, or None if missing."""
    path = os.path.join(feature_dir, "pyproject.toml")
    if os.path.isfile(path):
        return os.path.getmtime(path)
    return None


def _newest_source_mtime(feature_dir: str) -> float:
    """Return the mtime of the most recently modified source file."""
    newest = 0.0
    src_dir = os.path.join(feature_dir, "src")
    if not os.path.isdir(src_dir):
        return newest
    for root, _, files in os.walk(src_dir):
        # Skip test directories
        if "/tests" in root:
            continue
        for f in files:
            if f.endswith((".py", ".html")):
                mtime = os.path.getmtime(os.path.join(root, f))
                if mtime > newest:
                    newest = mtime
    return newest


def is_contract_stale(feature_dir: str) -> bool:
    """Return True if source files are newer than pyproject.toml."""
    pyproject_mt = _pyproject_mtime(feature_dir)
    if pyproject_mt is None:
        return False
    source_mt = _newest_source_mtime(feature_dir)
    return source_mt > pyproject_mt


def check_and_refresh_contracts(
    workspace: str,
    features: list[str],
    *,
    interactive: bool = True,
) -> list[str]:
    """Check all features for stale contracts. Offer to refresh interactively.

    Args:
        workspace: workspace root path
        features: list of feature entries from pyproject.toml
        interactive: if True, prompt user to update stale contracts

    Returns:
        List of feature names that were updated.
    """
    from splent_cli.utils.feature_utils import parse_feature_entry

    stale = []
    for entry in features:
        ns_safe, name, version = parse_feature_entry(entry)

        # Only check editable features (workspace root) — pinned are immutable
        feature_dir = os.path.join(workspace, name)
        if not os.path.isdir(feature_dir):
            continue

        if is_contract_stale(feature_dir):
            stale.append((name, ns_safe, feature_dir))

    if not stale:
        return []

    click.echo()
    click.secho(
        f"  ⚠ {len(stale)} feature(s) with potentially stale contracts:",
        fg="yellow",
        bold=True,
    )
    for name, _, _ in stale:
        click.echo(f"    - {name}")

    if not interactive:
        click.echo(
            "    Run: splent feature:contract <feature> --write"
        )
        click.echo()
        return []

    update = click.confirm(
        "\n  Update stale contracts now?", default=True
    )

    if not update:
        click.echo()
        return []

    updated = []
    for name, ns_safe, feature_dir in stale:
        try:
            from splent_cli.commands.feature.feature_contract import update_contract
            update_contract(feature_dir, ns_safe, name)
            click.secho(f"    ✔ {name} contract updated.", fg="green")
            updated.append(name)
        except Exception as e:
            click.secho(f"    ✖ {name}: {e}", fg="red")

    click.echo()
    return updated
