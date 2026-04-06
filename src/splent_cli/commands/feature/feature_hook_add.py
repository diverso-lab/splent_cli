"""
splent feature:hook:add

Add a new template hook registration to a feature's hooks.py.
Also updates [tool.splent.contract.provides].hooks in pyproject.toml.
"""

import os
import re
import tomllib
import click
from pathlib import Path

from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_editable(feature_ref: str, workspace: str) -> tuple[Path, str, str]:
    """Resolve an editable (non-versioned) feature from the cache."""
    base, _, version = feature_ref.partition("@")
    if version:
        raise SystemExit(
            f"❌ feature:hook:add only works on editable features.\n"
            f"   Remove the version suffix: {base}"
        )

    if "/" in base:
        ns_raw, name = base.split("/", 1)
        ns = normalize_namespace(ns_raw)
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


def _parse_hooks(hooks_path: Path) -> list[tuple[str, str]]:
    """Return list of (slot, function_name) from hooks.py."""
    if not hooks_path.exists():
        return []
    text = hooks_path.read_text()
    return re.findall(
        r"""register_template_hook\s*\(\s*['"]([^'"]+)['"]\s*,\s*(\w+)\s*\)""",
        text,
    )


def _ensure_hooks_file(hooks_path: Path) -> None:
    """Create hooks.py with standard imports if it does not exist."""
    if not hooks_path.exists():
        hooks_path.write_text(
            "from splent_framework.hooks.template_hooks import register_template_hook\n"
            "from flask import render_template\n"
        )


def _append_hook(hooks_path: Path, slot: str, function_name: str) -> None:
    """Append a function stub and its hook registration to hooks.py."""
    current = hooks_path.read_text()
    if not current.endswith("\n"):
        current += "\n"

    addition = (
        f"\n\ndef {function_name}():\n"
        f'    return ""  # TODO: render your hook fragment here\n'
        f"\n\n"
        f'register_template_hook("{slot}", {function_name})\n'
    )
    hooks_path.write_text(current + addition)


def _update_contract(pyproject_path: Path, slot: str) -> None:
    """Add slot to [tool.splent.contract.provides].hooks in pyproject.toml."""
    if not pyproject_path.exists():
        return

    text = pyproject_path.read_text()
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return

    current = (
        data.get("tool", {})
        .get("splent", {})
        .get("contract", {})
        .get("provides", {})
        .get("hooks", [])
    )

    if slot in current:
        return

    updated = sorted(set(current) | {slot})
    hooks_toml = "[" + ", ".join(f'"{h}"' for h in updated) + "]"

    if re.search(r"^hooks\s*=", text, re.MULTILINE):
        text = re.sub(
            r"^hooks\s*=.*$",
            f"hooks      = {hooks_toml}",
            text,
            flags=re.MULTILINE,
        )
    else:
        # Insert after commands = [...] line
        text = re.sub(
            r"(^commands\s*=.*$)",
            rf"\1\nhooks      = {hooks_toml}",
            text,
            flags=re.MULTILINE,
        )

    pyproject_path.write_text(text)


# ─────────────────────────────────────────────────────────────────────────────
# Command
# ─────────────────────────────────────────────────────────────────────────────


@click.command(
    "feature:hook:add",
    short_help="Add a new template hook to a feature's hooks.py.",
)
@click.argument("feature_ref")
@click.argument("slot")
@click.argument("function_name")
@context.requires_product
def feature_hook_add(feature_ref, slot, function_name):
    """
    Add a new template hook registration to a feature's hooks.py.

    \b
    FEATURE_REF    splent_feature_auth or splent-io/splent_feature_auth
    SLOT           Hook slot name, e.g.  layout.sidebar
    FUNCTION_NAME  Python function name for the hook callback

    \b
    The command:
      1. Adds an import if hooks.py does not exist yet.
      2. Appends a function stub for FUNCTION_NAME.
      3. Appends register_template_hook(SLOT, FUNCTION_NAME).
      4. Updates [tool.splent.contract.provides].hooks in pyproject.toml.

    \b
    Example:
      splent feature:hook:add splent_feature_notes layout.sidebar render_notes_sidebar
    """
    workspace = str(context.workspace())
    cache_path, ns, name = _resolve_editable(feature_ref, workspace)

    hooks_path = cache_path / "src" / ns / name / "hooks.py"
    existing = _parse_hooks(hooks_path)
    existing_slots = {s for s, _ in existing}
    existing_funcs = {f for _, f in existing}

    if slot in existing_slots:
        click.secho(
            f"❌ Slot '{slot}' is already registered in hooks.py.\n"
            f"   Run  splent feature:hooks {name}  to see current hooks.",
            fg="red",
        )
        raise SystemExit(1)

    if function_name in existing_funcs:
        click.secho(
            f"❌ Function name '{function_name}' is already defined in hooks.py.\n"
            f"   Choose a different function name.",
            fg="red",
        )
        raise SystemExit(1)

    _ensure_hooks_file(hooks_path)
    _append_hook(hooks_path, slot, function_name)
    click.secho(f"✅ Hook '{slot}' → {function_name}() added to hooks.py.", fg="green")

    _update_contract(cache_path / "pyproject.toml", slot)
    click.secho("✅ Contract updated in pyproject.toml.", fg="green")

    click.echo()
    click.echo("  Implement your hook at:")
    click.echo(f"  {hooks_path}")
    click.echo()


cli_command = feature_hook_add
