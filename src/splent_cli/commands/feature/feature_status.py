"""
splent feature:status

Displays the lifecycle state of every feature tracked in splent.manifest.json.
If the manifest does not exist yet, falls back to pyproject.toml and shows
all features as 'declared'.

States
──────
  declared   Feature added to the product, not yet pip-installed
  installed  pip-installed and importable
  migrated   Database migrations applied
  active     Flask is running with blueprints registered
  disabled   Installed but not activated at runtime
"""

import os
import tomllib
import click
from splent_cli.services import context
from splent_cli.utils.manifest import (
    read_manifest,
    manifest_exists,
    STATE_COLORS,
    STATES,
)


def _read_pyproject_features(product_path: str) -> list[str]:
    """Fallback: read feature list from pyproject.toml."""
    pyproject = os.path.join(product_path, "pyproject.toml")
    if not os.path.exists(pyproject):
        return []
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    return (
        data.get("project", {})
        .get("optional-dependencies", {})
        .get("features", [])
    )


def _state_badge(state: str) -> str:
    color = STATE_COLORS.get(state, "white")
    return click.style(f" {state:<10}", fg=color, bold=(state == "active"))


def _progress_bar(state: str) -> str:
    """Return a compact ASCII progress indicator for the 4 core states."""
    idx = STATES.index(state) if state in STATES else -1
    parts = []
    for i, s in enumerate(STATES):
        if i < idx:
            parts.append(click.style("●", fg="green"))
        elif i == idx:
            parts.append(click.style("●", fg=STATE_COLORS.get(state, "white"), bold=True))
        else:
            parts.append(click.style("○", fg="bright_black"))
    return "─".join(parts)


@click.command(
    "feature:status",
    short_help="Show lifecycle state of all features in the active product.",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw manifest as JSON.")
def feature_status(as_json):
    """
    Show the lifecycle state of every feature tracked in splent.manifest.json.

    If the manifest does not exist, falls back to pyproject.toml and reports
    all features as 'declared' (the only state the CLI can infer without running).

    Run `splent product:run` and the startup scripts to advance states beyond
    'declared'. The manifest is updated automatically as the product boots.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)

    if as_json:
        import json
        click.echo(json.dumps(read_manifest(product_path), indent=2))
        return

    click.echo()
    click.echo(click.style(f"  Feature status — {product}", bold=True))
    click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

    if manifest_exists(product_path):
        manifest = read_manifest(product_path)
        features = manifest.get("features", {})

        if not features:
            click.echo("  No features tracked in manifest.")
            click.echo()
            return

        # Header
        click.echo(
            f"  {'Feature':<42} {'Mode':<9} {'Progress':<11}  State"
        )
        click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

        for key, entry in sorted(features.items()):
            name = entry.get("name", key)
            version = entry.get("version") or "editable"
            mode = entry.get("mode", "editable")
            state = entry.get("state", "declared")

            label = f"{entry.get('namespace', '')}/{name}"
            if version != "editable":
                label += f"@{version}"

            progress = _progress_bar(state)
            state_str = _state_badge(state)

            click.echo(f"  {label:<42} {mode:<9} {progress}  {state_str}")

        click.echo()
        updated = manifest.get("updated_at", "—")
        click.echo(click.style(f"  Last updated: {updated}", fg="bright_black"))

    else:
        # No manifest — fallback to pyproject.toml
        click.secho(
            "  ⚠  splent.manifest.json not found — reading pyproject.toml (all states inferred as 'declared').",
            fg="yellow",
        )
        click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

        entries = _read_pyproject_features(product_path)
        if not entries:
            click.echo("  No features found in pyproject.toml.")
            click.echo()
            return

        click.echo(f"  {'Feature':<50} State")
        click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

        for entry in entries:
            state_str = _state_badge("declared")
            click.echo(f"  {entry:<50} {state_str}")

        click.echo()
        click.secho(
            "  Run `splent feature:add` or `splent feature:attach` to start tracking states.",
            fg="bright_black",
        )

    click.echo()


cli_command = feature_status
