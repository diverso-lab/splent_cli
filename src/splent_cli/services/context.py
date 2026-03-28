import functools
import os
from pathlib import Path

import click


def workspace() -> Path:
    """Return the SPLENT workspace root (WORKING_DIR env var)."""
    path = Path(os.getenv("WORKING_DIR", "/workspace"))
    if not path.exists():
        click.secho(
            f"❌ Workspace not found: {path}\n"
            f"   Set WORKING_DIR to the correct path"
            f" or run: source .env",
            fg="red",
        )
        raise SystemExit(1)
    return path


def require_app() -> str:
    """Return SPLENT_APP or exit with an error message."""
    product = os.getenv("SPLENT_APP")
    if not product:
        click.secho("❌ SPLENT_APP not set.", fg="red")
        raise SystemExit(1)
    return product


def active_app() -> str | None:
    """Return SPLENT_APP or None if not set (no error)."""
    return os.getenv("SPLENT_APP") or None


def is_detached() -> bool:
    """True when no product is selected (detached mode)."""
    return active_app() is None


def resolve_env(env_dev: bool = False, env_prod: bool = False) -> str:
    """Resolve the target environment from CLI flags or SPLENT_ENV, defaulting to 'dev'."""
    return "prod" if env_prod else "dev" if env_dev else os.getenv("SPLENT_ENV", "dev")


# ---------------------------------------------------------------------------
# Decorators for Click commands
# ---------------------------------------------------------------------------


def requires_product(fn):
    """Decorator: abort if no product is selected (SPLENT_APP not set).

    Use on commands that operate on the active product (derive, up, run, …).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if is_detached():
            click.secho(
                "❌ No product selected.\n"
                "   Run: splent product:select <name>",
                fg="red",
            )
            raise SystemExit(1)
        return fn(*args, **kwargs)

    return wrapper


def requires_detached(fn):
    """Decorator: abort if a product IS selected.

    Use on commands that must run in detached mode (create, list, select, …).
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        app = active_app()
        if app:
            click.secho(
                f"❌ A product is currently selected: {app}\n"
                f"   Run: splent product:deselect",
                fg="red",
            )
            raise SystemExit(1)
        return fn(*args, **kwargs)

    return wrapper
