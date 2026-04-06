"""
splent feature:hook:remove

Remove a template hook registration from a feature's hooks.py.
Optionally also removes the callback function definition.
Updates [tool.splent.contract.provides].hooks in pyproject.toml.
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
            f"❌ feature:hook:remove only works on editable features.\n"
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
        raise SystemExit(f"❌ Editable feature not found: {cache_path}")
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


def _remove_registration_line(text: str, slot: str, func: str) -> str:
    """Remove the register_template_hook(...) line for a specific slot+func."""
    lines = text.splitlines(keepends=True)
    pattern = re.compile(
        rf"""register_template_hook\s*\(\s*['"]({re.escape(slot)})['"]\s*,\s*{re.escape(func)}\s*\)"""
    )
    return "".join(line for line in lines if not pattern.search(line))


def _remove_function_body(text: str, func: str) -> str:
    """
    Remove the definition of `func` from the source text.

    Handles:
      def func_name():
          ...body...
      (stops at next top-level def/class or EOF)
    """
    # Match the def block at module level (0 indentation)
    pattern = re.compile(
        rf"(?:^|\n\n)def {re.escape(func)}\s*\([^)]*\):(?:\n(?:[ \t]+.*|\s*))*",
        re.MULTILINE,
    )
    return pattern.sub("", text)


def _remove_trailing_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines into 2."""
    return re.sub(r"\n{3,}", "\n\n", text)


def _update_contract(pyproject_path: Path, slot: str) -> None:
    """Remove slot from [tool.splent.contract.provides].hooks in pyproject.toml."""
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

    updated = sorted(h for h in current if h != slot)
    hooks_toml = "[" + ", ".join(f'"{h}"' for h in updated) + "]"

    if re.search(r"^hooks\s*=", text, re.MULTILINE):
        text = re.sub(
            r"^hooks\s*=.*$",
            f"hooks      = {hooks_toml}",
            text,
            flags=re.MULTILINE,
        )
        pyproject_path.write_text(text)


# ─────────────────────────────────────────────────────────────────────────────
# Command
# ─────────────────────────────────────────────────────────────────────────────


@click.command(
    "feature:hook:remove",
    short_help="Remove a template hook from a feature's hooks.py.",
)
@click.argument("feature_ref")
@click.argument("slot")
@click.option(
    "--with-function",
    is_flag=True,
    help="Also remove the hook callback function definition.",
)
@click.option("-y", "--yes", is_flag=True, help="Skip confirmation prompt.")
@context.requires_product
def feature_hook_remove(feature_ref, slot, with_function, yes):
    """
    Remove a template hook registration from a feature's hooks.py.

    \b
    FEATURE_REF   splent_feature_auth or splent-io/splent_feature_auth
    SLOT          Hook slot name to remove, e.g.  layout.sidebar

    By default only the register_template_hook(...) call is removed.
    Use --with-function to also delete the callback function body.

    \b
    Example:
      splent feature:hook:remove splent_feature_notes layout.sidebar
      splent feature:hook:remove splent_feature_notes layout.sidebar --with-function -y
    """
    workspace = str(context.workspace())
    cache_path, ns, name = _resolve_editable(feature_ref, workspace)

    hooks_path = cache_path / "src" / ns / name / "hooks.py"
    hooks = _parse_hooks(hooks_path)
    matches = [(s, f) for s, f in hooks if s == slot]

    if not matches:
        click.secho(
            f"❌ Hook slot '{slot}' not found in hooks.py.\n"
            f"   Run  splent feature:hooks {name}  to see registered hooks.",
            fg="red",
        )
        raise SystemExit(1)

    _, func_name = matches[0]

    if not yes:
        action = (
            f"Remove hook '{slot}' and function '{func_name}()' from {name}?"
            if with_function
            else f"Remove hook registration for slot '{slot}' from {name}?"
        )
        click.confirm(action, abort=True)

    text = hooks_path.read_text()

    # Always remove the register_template_hook(...) line
    text = _remove_registration_line(text, slot, func_name)

    if with_function:
        text = _remove_function_body(text, func_name)

    text = _remove_trailing_blank_lines(text)
    hooks_path.write_text(text)

    click.secho(f"✅ Hook registration '{slot}' removed from hooks.py.", fg="green")
    if with_function:
        click.secho(f"✅ Function '{func_name}()' removed.", fg="green")

    _update_contract(cache_path / "pyproject.toml", slot)
    click.secho("✅ Contract updated in pyproject.toml.", fg="green")
    click.echo()


cli_command = feature_hook_remove
