import os
from pathlib import Path

import click


def workspace() -> Path:
    """Return the SPLENT workspace root (WORKING_DIR env var, defaults to /workspace)."""
    return Path(os.getenv("WORKING_DIR", "/workspace"))


def require_app() -> str:
    """Return SPLENT_APP or exit with an error message."""
    product = os.getenv("SPLENT_APP")
    if not product:
        click.secho("❌ SPLENT_APP not set.", fg="red")
        raise SystemExit(1)
    return product


def resolve_env(env_dev: bool = False, env_prod: bool = False) -> str:
    """Resolve the target environment from CLI flags or SPLENT_ENV, defaulting to 'dev'."""
    return "prod" if env_prod else "dev" if env_dev else os.getenv("SPLENT_ENV", "dev")
