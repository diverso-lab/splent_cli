"""
Feature lifecycle state machine — single source of truth for valid transitions.

State machine
─────────────

    (absent) → [cached] → [declared] → [installed] → [migrated] → [active]
                                                                       ↕
                                                                   [disabled]

Each transition has:
  - A minimum required state (precondition)
  - A target state (postcondition)
  - An optional list of blocked states

Usage from any CLI command:

    from splent_cli.utils.lifecycle import require_state, advance_state

    # Guard: abort if feature is not at least "installed"
    require_state(product_path, key, min_state="installed", command="db:migrate")

    # Advance: set state to "migrated" after successful migration
    advance_state(product_path, product, key, to="migrated", **feature_info)
"""

import click

from splent_cli.utils.manifest import (
    STATES,
    VALID_STATES,
    STATE_COLORS,
    get_feature_state,
    set_feature_state,
    read_manifest,
    feature_key,
)


# ---------------------------------------------------------------------------
# State ordering (index = rank)
# ---------------------------------------------------------------------------

_STATE_RANK: dict[str, int] = {s: i for i, s in enumerate(STATES)}
_STATE_RANK["disabled"] = _STATE_RANK["active"]  # disabled ≈ active (installed + migrated)


def state_rank(state: str | None) -> int:
    """Return the numeric rank of a state (-1 for unknown/None)."""
    if state is None:
        return -1
    return _STATE_RANK.get(state, -1)


# ---------------------------------------------------------------------------
# Valid transitions table
# ---------------------------------------------------------------------------

# For each target state, what is the minimum required current state?
# None means "any state is fine" (initial declaration).
REQUIRED_MIN_STATE: dict[str, str | None] = {
    "declared": None,           # feature:add / feature:attach
    "installed": "declared",    # pip install
    "migrated": "installed",    # db:upgrade / db:migrate
    "active": "migrated",      # Flask startup
    "disabled": "installed",    # can disable if at least installed
}

# States that block certain operations (command → blocked states)
BLOCKED_STATES: dict[str, set[str]] = {
    "feature:add": set(),                      # always ok
    "feature:attach": set(),                   # always ok
    "feature:remove": {"migrated", "active"},  # must rollback first
    "feature:detach": {"migrated", "active"},  # must rollback first
    "feature:edit": {"migrated", "active"},    # must rollback first
    "feature:upgrade": {"migrated", "active"}, # must rollback first
    "feature:rename": {"migrated", "active"},  # must rollback first
    "db:migrate": set(),                       # needs "installed" min (handled by require_state)
    "db:upgrade": set(),                       # needs "installed" min
    "db:rollback": set(),                      # needs "migrated" min
    "db:seed": set(),                          # needs "migrated" min
}

# Guidance messages for blocked states
BLOCKED_GUIDANCE: dict[str, str] = {
    "migrated": "Run 'splent db:rollback <feature>' first to undo migrations.",
    "active": "Stop the application first, then run 'splent db:rollback <feature>'.",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def require_state(
    product_path: str,
    key: str,
    *,
    min_state: str | None = None,
    command: str = "",
    force: bool = False,
) -> str | None:
    """Validate that a feature is in an acceptable state for a command.

    Args:
        product_path: Path to the product directory.
        key: Manifest key (from feature_key()).
        min_state: Minimum required state (e.g., "installed" for db:migrate).
        command: Command name for error messages and blocked-state lookup.
        force: If True, warn but don't abort.

    Returns:
        The current state (str) or None if not tracked.

    Raises:
        SystemExit if the feature is in a blocked or insufficient state (unless force=True).
    """
    current = get_feature_state(product_path, key)

    # Check blocked states for this command
    blocked = BLOCKED_STATES.get(command, set())
    if current in blocked:
        guidance = BLOCKED_GUIDANCE.get(current, "")
        msg = (
            f"Feature '{key}' is in state '{current}' — "
            f"cannot run '{command}'."
        )
        if guidance:
            msg += f"\n   {guidance}"

        if force:
            click.secho(f"⚠️  {msg} (--force: continuing anyway)", fg="yellow")
        else:
            click.secho(f"❌ {msg}", fg="red")
            raise SystemExit(1)

    # Check minimum state
    if min_state is not None and current is not None:
        if state_rank(current) < state_rank(min_state):
            msg = (
                f"Feature '{key}' is in state '{current}' but '{command}' "
                f"requires at least '{min_state}'."
            )
            if force:
                click.secho(f"⚠️  {msg} (--force: continuing anyway)", fg="yellow")
            else:
                click.secho(f"❌ {msg}", fg="red")
                raise SystemExit(1)

    return current


def advance_state(
    product_path: str,
    product_name: str,
    key: str,
    *,
    to: str,
    namespace: str,
    name: str,
    version: str | None = None,
    mode: str | None = None,
) -> None:
    """Advance a feature to a new lifecycle state in the manifest.

    Only advances forward — will not regress state unless explicitly
    moving to "declared" (rollback) or "disabled".

    Args:
        to: Target state.
        Other args: passed through to set_feature_state().
    """
    current = get_feature_state(product_path, key)

    # Determine mode from existing entry if not provided
    if mode is None:
        manifest = read_manifest(product_path)
        entry = manifest.get("features", {}).get(key, {})
        mode = entry.get("mode", "pinned" if version else "editable")

    # Only advance forward (higher rank), or allow explicit regression for rollback/disable
    if to in ("declared", "disabled") or current is None or state_rank(to) > state_rank(current):
        set_feature_state(
            product_path,
            product_name,
            key,
            to,
            namespace=namespace,
            name=name,
            version=version,
            mode=mode,
        )


def resolve_feature_key_from_entry(entry: str) -> tuple[str, str, str, str | None]:
    """Parse a pyproject feature entry into (key, namespace, name, version).

    Handles formats like:
        "splent-io/splent_feature_auth@v1.2.2"
        "splent_feature_auth@v1.2.2"
        "splent_feature_auth"
    """
    raw = entry.strip()
    if "/" in raw:
        org_raw, rest = raw.split("/", 1)
        ns = org_raw.replace("-", "_").replace(".", "_")
    else:
        ns = "splent_io"
        rest = raw

    name, _, version = rest.partition("@")
    version = version or None
    key = feature_key(ns, name, version)
    return key, ns, name, version


def require_editable(
    product_path: str,
    key: str,
    *,
    command: str = "",
) -> None:
    """Abort if the feature is pinned (read-only).

    Pinned features are versioned snapshots that must not be modified
    in place.  Use ``feature:edit`` to create an editable copy first.
    """
    manifest = read_manifest(product_path)
    entry = manifest.get("features", {}).get(key, {})
    mode = entry.get("mode", "editable")

    if mode == "pinned":
        click.secho(
            f"❌ Feature '{key}' is pinned (read-only). "
            f"Run 'splent feature:edit {entry.get('name', key)}' first to create an editable copy.",
            fg="red",
        )
        raise SystemExit(1)
