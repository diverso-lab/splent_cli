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
import click
from splent_cli.services import context
from splent_cli.utils.feature_utils import load_product_features
from splent_cli.utils.manifest import (
    read_manifest,
    manifest_exists,
    STATE_COLORS,
    STATES,
)


def _state_badge(state: str) -> str:
    color = STATE_COLORS.get(state, "white")
    return click.style(f" {state:<10}", fg=color, bold=(state == "active"))


def _progress_bar(state: str, has_migrations: bool = True) -> str:
    """Return a compact ASCII progress indicator for the 4 core states.

    If has_migrations is False, the 'migrated' step is shown as skipped (grey)
    even if the feature reached 'active'.
    """
    idx = STATES.index(state) if state in STATES else -1
    migrated_idx = STATES.index("migrated")
    parts = []
    for i, s in enumerate(STATES):
        # Skip migrated step if feature has no migrations
        if i == migrated_idx and not has_migrations and idx >= migrated_idx:
            parts.append(click.style("○", fg="bright_black"))
            continue
        if i < idx:
            parts.append(click.style("●", fg="green"))
        elif i == idx:
            parts.append(
                click.style("●", fg=STATE_COLORS.get(state, "white"), bold=True)
            )
        else:
            parts.append(click.style("○", fg="bright_black"))
    return "─".join(parts)


# ── Timeline renderer ─────────────────────────────────────────────────

_LANE_COLORS = ["cyan", "yellow", "magenta", "green", "blue", "red", "bright_cyan", "bright_yellow"]

_STATE_SYMBOLS = {
    "declared": "○",
    "installed": "◐",
    "migrated": "◑",
    "active": "●",
}


def _render_timeline(product, product_path):
    """Render a GitKraken-style multi-lane timeline of feature state transitions."""
    from datetime import datetime

    if not manifest_exists(product_path):
        click.secho("  No manifest found — run product:derive first.", fg="yellow")
        return

    manifest = read_manifest(product_path)
    all_features = manifest.get("features", {})

    # Filter to active keys (from pyproject)
    from splent_cli.utils.lifecycle import resolve_feature_key_from_entry
    try:
        pyproject_entries = load_product_features(product_path, os.getenv("SPLENT_ENV"))
    except FileNotFoundError:
        pyproject_entries = []
    active_keys = set()
    for entry in pyproject_entries:
        key, _, _, _ = resolve_feature_key_from_entry(entry)
        active_keys.add(key)

    features = {k: v for k, v in all_features.items() if k in active_keys}
    if not features:
        click.secho("  No features to display.", fg="yellow")
        return

    # Build sorted feature list for lane assignment
    feature_names = []
    feature_data = {}
    for key, entry in sorted(features.items()):
        short = entry.get("name", key).replace("splent_feature_", "")
        feature_names.append(short)
        feature_data[short] = entry

    num_lanes = len(feature_names)

    # Collect all events: (timestamp, feature_short, state)
    events = []
    for short, entry in feature_data.items():
        for state_key, state_name in [
            ("declared_at", "declared"),
            ("installed_at", "installed"),
            ("migrated_at", "migrated"),
        ]:
            ts = entry.get(state_key)
            if ts:
                events.append((ts, short, state_name))
        # active = updated_at if state is active
        if entry.get("state") == "active":
            events.append((entry.get("updated_at", ""), short, "active"))

    # Sort by timestamp
    events.sort(key=lambda e: e[0])

    # Group events by timestamp (same second = same row)
    grouped = []
    current_ts = None
    current_group = []
    for ts, short, state in events:
        ts_short = ts[:19] if ts else ""  # trim to seconds
        if ts_short != current_ts:
            if current_group:
                grouped.append((current_ts, current_group))
            current_ts = ts_short
            current_group = []
        current_group.append((short, state))
    if current_group:
        grouped.append((current_ts, current_group))

    # Render
    click.echo()
    click.secho(f"  Feature timeline — {product}", bold=True)
    click.echo()

    # Lane headers
    lane_width = max(len(n) for n in feature_names) + 2
    time_col = 10
    header_parts = [f"  {'TIME':<{time_col}}"]
    for i, name in enumerate(feature_names):
        color = _LANE_COLORS[i % len(_LANE_COLORS)]
        header_parts.append(click.style(f"{name:^{lane_width}}", fg=color, bold=True))
    click.echo("".join(header_parts))
    click.echo("  " + "─" * (time_col + lane_width * num_lanes))

    # Render each time row
    prev_ts = None
    for ts, group_events in grouped:
        # Time label (show only HH:MM:SS)
        try:
            dt = datetime.fromisoformat(ts)
            time_str = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            time_str = ts[:8] if ts else "??:??:??"

        # Show time gap
        if prev_ts:
            try:
                prev_dt = datetime.fromisoformat(prev_ts)
                curr_dt = datetime.fromisoformat(ts)
                gap = (curr_dt - prev_dt).total_seconds()
                if gap > 5:
                    # Empty row with just pipes
                    gap_parts = [f"  {'':>{time_col}}"]
                    for i in range(num_lanes):
                        color = _LANE_COLORS[i % len(_LANE_COLORS)]
                        gap_parts.append(click.style(f"{'│':^{lane_width}}", fg=color))
                    click.echo("".join(gap_parts))

                    if gap > 30:
                        gap_label = f"+{int(gap)}s"
                        gap_parts2 = [f"  {click.style(gap_label, fg='bright_black'):>{time_col + 9}}"]
                        for i in range(num_lanes):
                            color = _LANE_COLORS[i % len(_LANE_COLORS)]
                            gap_parts2.append(click.style(f"{'│':^{lane_width}}", fg=color))
                        click.echo("".join(gap_parts2))
            except (ValueError, TypeError):
                pass

        prev_ts = ts

        # Build the event set for this row
        event_map = {short: state for short, state in group_events}

        row_parts = [click.style(f"  {time_str:<{time_col}}", fg="bright_black")]
        for i, name in enumerate(feature_names):
            color = _LANE_COLORS[i % len(_LANE_COLORS)]
            if name in event_map:
                state = event_map[name]
                symbol = _STATE_SYMBOLS.get(state, "?")
                state_color = STATE_COLORS.get(state, "white")
                node = click.style(symbol, fg=state_color, bold=True)
                label = click.style(f" {state}", fg=state_color)
                # Pad considering invisible ANSI codes
                cell = f"{node}{label}"
                visible_len = len(symbol) + 1 + len(state)
                padding = lane_width - visible_len
                cell += " " * max(padding, 0)
            else:
                cell = click.style(f"{'│':^{lane_width}}", fg=color)
            row_parts.append(cell)

        click.echo("".join(row_parts))

    # Final state indicators
    click.echo("  " + "─" * (time_col + lane_width * num_lanes))
    final_parts = [f"  {'NOW':<{time_col}}"]
    for i, name in enumerate(feature_names):
        state = feature_data[name].get("state", "?")
        color = STATE_COLORS.get(state, "white")
        symbol = _STATE_SYMBOLS.get(state, "?")
        cell = click.style(f"{symbol} {state}", fg=color, bold=True)
        visible_len = len(symbol) + 1 + len(state)
        padding = lane_width - visible_len
        cell += " " * max(padding, 0)
        final_parts.append(cell)
    click.echo("".join(final_parts))

    # Legend
    click.echo()
    click.echo(
        "  "
        + "  ".join(
            click.style(f"{sym} {name}", fg=STATE_COLORS.get(name, "white"))
            for name, sym in _STATE_SYMBOLS.items()
        )
    )
    click.echo()


@click.command(
    "feature:status",
    short_help="Show lifecycle state of all features in the active product.",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw manifest as JSON.")
@click.option("--integrity", is_flag=True, help="Verify manifest against actual system state.")
@click.option("--fix", "do_fix", is_flag=True, help="Fix detected integrity issues.")
@click.option("--timeline", is_flag=True, help="Show lifecycle timeline (GitKraken style).")
def feature_status(as_json, integrity, do_fix, timeline):
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

    if timeline:
        _render_timeline(product, product_path)
        return

    click.echo()
    click.echo(click.style(f"  Feature status — {product}", bold=True))
    click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

    if manifest_exists(product_path):
        manifest = read_manifest(product_path)
        all_features = manifest.get("features", {})

        # Filter: only show features declared in pyproject.toml
        from splent_cli.utils.lifecycle import resolve_feature_key_from_entry

        try:
            pyproject_entries = load_product_features(product_path, os.getenv("SPLENT_ENV"))
        except FileNotFoundError:
            pyproject_entries = []
        active_keys = set()
        for entry in pyproject_entries:
            key, _, _, _ = resolve_feature_key_from_entry(entry)
            active_keys.add(key)

        features = {k: v for k, v in all_features.items() if k in active_keys}

        if not features:
            click.echo("  No features tracked in manifest.")
            click.echo()
            return

        # Build rows and compute dynamic column width
        rows = []
        from splent_framework.managers.migration_manager import MigrationManager

        for key, entry in sorted(features.items()):
            name = entry.get("name", key)
            version = entry.get("version") or "editable"
            mode = entry.get("mode", "editable")
            state = entry.get("state", "declared")
            label = f"{entry.get('namespace', '')}/{name}"
            if version != "editable":
                label += f"@{version}"
            # Check if feature has migrations
            mdir = MigrationManager.get_feature_migration_dir(name)
            has_migrations = mdir is not None and os.path.isdir(mdir)
            rows.append((label, mode, state, has_migrations))

        col_w = max(len(r[0]) for r in rows)
        col_w = max(col_w, len("Feature")) + 2
        total_w = col_w + 10 + 9 + 12

        # Header
        click.echo(f"  {'Feature':<{col_w}} {'Mode':<10}{'Progress':<9}  State")
        click.echo(click.style(f"  {'─' * total_w}", fg="bright_black"))

        for label, mode, state, has_mig in rows:
            progress = _progress_bar(state, has_migrations=has_mig)
            state_str = _state_badge(state)
            mode_color = "cyan" if mode == "pinned" else "magenta"
            mode_str = click.style(f"{mode:<10}", fg=mode_color)
            click.echo(f"  {label:<{col_w}} {mode_str}{progress}  {state_str}")

        # Legend
        click.echo()
        click.echo(click.style(f"  {'─' * total_w}", fg="bright_black"))
        click.echo(
            click.style("  Progress: ", fg="bright_black")
            + " → ".join(
                click.style(s, fg=STATE_COLORS.get(s, "white")) for s in STATES
            )
        )
        click.echo(
            click.style("  Modes:    ", fg="bright_black")
            + click.style("pinned", fg="cyan")
            + " = versioned release   "
            + click.style("editable", fg="magenta")
            + " = local development"
        )

        click.echo()
        updated = manifest.get("updated_at", "—")
        click.echo(click.style(f"  Last updated: {updated}", fg="bright_black"))

        # Report stale entries
        stale = len(all_features) - len(features)
        if stale > 0:
            click.echo(
                click.style(
                    f"  ({stale} stale entries hidden — run 'splent product:sync' to clean up)",
                    fg="bright_black",
                )
            )

        # ── Integrity check ──────────────────────────────────────────
        if integrity or do_fix:
            from splent_cli.utils.integrity import check_feature_integrity, fix_feature

            click.echo()
            click.secho("  Integrity check", bold=True)
            click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

            total_ok = total_fail = 0

            for key, entry in sorted(features.items()):
                name = entry.get("name", key)
                ns_safe = entry.get("namespace", "splent_io")
                version = entry.get("version")
                state = entry.get("state", "declared")

                results = check_feature_integrity(
                    product_path, ns_safe, name, version, state
                )

                has_issues = any(not r["ok"] for r in results)
                name_color = "red" if has_issues else "green"
                click.echo(f"\n  {click.style(name, fg=name_color, bold=True)}")

                for r in results:
                    if r["ok"]:
                        total_ok += 1
                        click.echo(
                            click.style("    [✔] ", fg="green")
                            + f"{r['check']}: {r['detail']}"
                        )
                    else:
                        total_fail += 1
                        click.echo(
                            click.style("    [✖] ", fg="red")
                            + f"{r['check']}: {r['detail']}"
                        )

                if has_issues and do_fix:
                    issues = [r for r in results if not r["ok"]]
                    fix_feature(product_path, workspace, ns_safe, name, version, issues)

            click.echo()
            if total_fail == 0:
                click.secho(f"  ✅ All {total_ok} checks passed.", fg="green")
            else:
                click.secho(
                    f"  {total_ok} passed, {total_fail} failed.",
                    fg="red" if total_fail else "green",
                )

    else:
        # No manifest — fallback to pyproject.toml
        click.secho(
            "  ⚠  splent.manifest.json not found — reading pyproject.toml (all states inferred as 'declared').",
            fg="yellow",
        )
        click.echo(click.style(f"  {'─' * 66}", fg="bright_black"))

        try:
            entries = load_product_features(product_path, os.getenv("SPLENT_ENV"))
        except FileNotFoundError:
            entries = []
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
