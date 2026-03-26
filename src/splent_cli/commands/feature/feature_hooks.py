"""
splent feature:hooks

List all template hook registrations declared in a feature's hooks.py.
"""

import json
import os
import re
import click
from pathlib import Path

from splent_cli.services import context


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_feature(feature_ref: str, workspace: str) -> tuple[Path, str, str, str | None]:
    """
    Resolve a feature_ref to (cache_path, ns, name, version).

    Accepts:
      splent_feature_auth
      splent-io/splent_feature_auth
      splent_feature_auth@v1.1.1
      splent-io/splent_feature_auth@v1.1.1
    """
    base, _, version = feature_ref.partition("@")
    version = version or None

    if "/" in base:
        ns_raw, name = base.split("/", 1)
        ns = ns_raw.replace("-", "_")
    else:
        ns = DEFAULT_NAMESPACE
        name = base

    cache_root = Path(workspace) / ".splent_cache" / "features" / ns

    if version:
        candidate = cache_root / f"{name}@{version}"
        if candidate.exists():
            return candidate, ns, name, version
        raise SystemExit(
            f"❌ Versioned feature not found in cache: {candidate}\n"
            f"   Run: splent feature:attach {ns.replace('_', '-')}/{name} {version}"
        )

    # Editable feature at workspace root
    ws_root = Path(workspace) / name
    if ws_root.exists():
        return ws_root, ns, name, None

    # Legacy: editable in cache
    candidate = cache_root / name
    if candidate.exists():
        return candidate, ns, name, None

    raise SystemExit(
        f"❌ Feature not found at workspace root or cache: {name}\n"
        f"   Run: splent feature:create {ns.replace('_', '-')}/{name}"
    )


def _parse_hooks(hooks_path: Path) -> list[dict]:
    """Return list of {slot, function} dicts parsed from hooks.py."""
    if not hooks_path.exists():
        return []
    text = hooks_path.read_text()
    matches = re.findall(
        r"""register_template_hook\s*\(\s*['"]([^'"]+)['"]\s*,\s*(\w+)\s*\)""",
        text,
    )
    return [{"slot": slot, "function": func} for slot, func in matches]


# ─────────────────────────────────────────────────────────────────────────────
# Command
# ─────────────────────────────────────────────────────────────────────────────

@click.command(
    "feature:hooks",
    short_help="List template hooks registered by a feature.",
)
@click.argument("feature_ref")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON array.")
def feature_hooks(feature_ref, as_json):
    """
    List all template hook registrations declared in a feature's hooks.py.

    \b
    FEATURE_REF accepts:
      splent_feature_auth
      splent-io/splent_feature_auth
      splent_feature_auth@v1.1.1

    Example:
      splent feature:hooks splent_feature_auth
    """
    workspace = str(context.workspace())
    cache_path, ns, name, version = _resolve_feature(feature_ref, workspace)

    hooks_path = cache_path / "src" / ns / name / "hooks.py"
    hooks = _parse_hooks(hooks_path)

    if as_json:
        click.echo(json.dumps(hooks, indent=2))
        return

    label = f"{name}@{version}" if version else name

    click.echo()
    click.echo(click.style(f"  Template hooks — {label}", bold=True))
    click.echo(click.style(f"  {'─' * 60}", fg="bright_black"))

    if not hooks:
        if not hooks_path.exists():
            click.echo(click.style("  hooks.py not found.", fg="yellow"))
        else:
            click.echo("  No hooks registered.")
    else:
        click.echo(f"  {'Slot':<42} Function")
        click.echo(click.style(f"  {'─' * 60}", fg="bright_black"))
        for h in hooks:
            slot_label = click.style(h["slot"], fg="cyan")
            click.echo(f"  {h['slot']:<42} {h['function']}")

    click.echo()
    click.echo(
        click.style("  Manage hooks with:", fg="bright_black")
        + f"  splent feature:hook:add {name} <slot> <function>"
    )
    click.echo()


cli_command = feature_hooks
