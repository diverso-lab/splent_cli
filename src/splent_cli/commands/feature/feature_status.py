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

_STATE_NODES = {
    "declared": ("○", "bright_black"),
    "installed": ("◐", "yellow"),
    "migrated": ("◑", "blue"),
    "active": ("●", "green"),
}

_FEATURE_COLORS = ["cyan", "yellow", "magenta", "green", "blue", "red"]


def _render_timeline(product, product_path):
    """Render a git-graph style vertical timeline of feature state transitions."""
    from datetime import datetime

    if not manifest_exists(product_path):
        click.secho("  No manifest found — run product:derive first.", fg="yellow")
        return

    manifest = read_manifest(product_path)
    all_features = manifest.get("features", {})

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

    # Assign colors to features
    feature_list = []
    color_map = {}
    for i, (key, entry) in enumerate(sorted(features.items())):
        short = entry.get("name", key).replace("splent_feature_", "")
        feature_list.append((short, entry))
        color_map[short] = _FEATURE_COLORS[i % len(_FEATURE_COLORS)]

    # Collect events: (timestamp, short_name, state)
    events = []
    for short, entry in feature_list:
        for ts_key, state in [
            ("declared_at", "declared"),
            ("installed_at", "installed"),
            ("migrated_at", "migrated"),
        ]:
            ts = entry.get(ts_key)
            if ts:
                events.append((ts, short, state))
        if entry.get("state") == "active":
            events.append((entry.get("updated_at", ""), short, "active"))

    events.sort(key=lambda e: (e[0], e[1]))

    # Group by timestamp (same second = same group)
    grouped = []
    cur_ts = None
    cur_group = []
    for ts, short, state in events:
        ts_sec = ts[:19] if ts else ""
        if ts_sec != cur_ts:
            if cur_group:
                grouped.append((cur_ts, cur_group))
            cur_ts = ts_sec
            cur_group = []
        cur_group.append((short, state))
    if cur_group:
        grouped.append((cur_ts, cur_group))

    # Render
    click.echo()
    click.secho(f"  Feature timeline — {product}", bold=True)
    click.echo()

    name_col = max(len(s) for s, _ in feature_list) + 2

    prev_ts = None
    for ts, group_events in grouped:
        # Time
        try:
            dt = datetime.fromisoformat(ts)
            time_str = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            time_str = "??:??:??"

        # Gap connector
        if prev_ts:
            try:
                gap = (
                    datetime.fromisoformat(ts) - datetime.fromisoformat(prev_ts)
                ).total_seconds()
                if gap > 2:
                    click.echo(click.style("            │", fg="bright_black"))
                    if gap > 30:
                        click.echo(
                            click.style(
                                f"            │  +{int(gap)}s", fg="bright_black"
                            )
                        )
                        click.echo(click.style("            │", fg="bright_black"))
            except (ValueError, TypeError):
                pass
        prev_ts = ts

        n = len(group_events)
        for idx, (short, state) in enumerate(group_events):
            node_sym, node_color = _STATE_NODES.get(state, ("?", "white"))
            feat_color = color_map.get(short, "white")

            # Tree connector
            if n == 1:
                connector = "──"
            elif idx == 0:
                connector = "┬─"
            elif idx == n - 1:
                connector = "└─"
            else:
                connector = "├─"

            # Time label only on first line of group
            if idx == 0:
                time_label = click.style(f"  {time_str}  ", fg="bright_black")
            else:
                time_label = click.style("           ", fg="bright_black")

            connector_styled = click.style(connector, fg="bright_black")
            node_styled = click.style(node_sym, fg=node_color, bold=True)
            name_styled = click.style(f" {short:<{name_col}}", fg=feat_color, bold=True)
            state_styled = click.style(state, fg=node_color)

            click.echo(
                f"{time_label}{connector_styled}{node_styled}{name_styled}{state_styled}"
            )

    # Final line
    click.echo(click.style("            │", fg="bright_black"))
    click.echo(
        click.style("  NOW     ", fg="bright_black")
        + click.style("──●", fg="green", bold=True)
        + click.style(f" {len(feature_list)} feature(s) ", fg="green", bold=True)
        + click.style(
            ", ".join(click.style(s, fg=color_map[s]) for s, _ in feature_list),
        )
    )

    # Legend
    click.echo()
    click.echo(
        "  "
        + "   ".join(
            click.style(f"{sym} {name}", fg=color)
            for name, (sym, color) in _STATE_NODES.items()
        )
    )
    click.echo()


@click.command(
    "feature:status",
    short_help="Show lifecycle state of all features in the active product.",
)
@click.option("--json", "as_json", is_flag=True, help="Output raw manifest as JSON.")
@click.option(
    "--integrity", is_flag=True, help="Verify manifest against actual system state."
)
@click.option("--fix", "do_fix", is_flag=True, help="Fix detected integrity issues.")
@click.option(
    "--timeline", is_flag=True, help="Show lifecycle timeline (GitKraken style)."
)
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
            pyproject_entries = load_product_features(
                product_path, os.getenv("SPLENT_ENV")
            )
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
                    f"  ({stale} stale entries hidden — run 'splent product:resolve' to clean up)",
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
