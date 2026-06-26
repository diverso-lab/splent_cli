"""
Safe filesystem helpers.

Two recurring "happy-path" hazards across the CLI:

  * parsing config (TOML/JSON) that is missing or malformed → raw
    ``FileNotFoundError`` / ``TOMLDecodeError`` / ``JSONDecodeError`` tracebacks
    that never say *which* file is broken.
  * rewriting config in place (pyproject.toml, .env, UVL) non-atomically and
    with no backup → a crash mid-write can corrupt or truncate the file.

These helpers give actionable errors and atomic, optionally-backed-up writes.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path

import click


def load_toml(path: str | os.PathLike, *, what: str | None = None) -> dict:
    """Load a TOML file, raising a clear ClickException on missing/invalid input."""
    p = Path(path)
    label = what or str(p)
    if not p.is_file():
        raise click.ClickException(f"{label} not found at: {p}")
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise click.ClickException(f"{label} is not valid TOML ({p}):\n  {e}")
    except OSError as e:
        raise click.ClickException(f"Could not read {label} ({p}): {e}")


def load_json(path: str | os.PathLike, *, what: str | None = None) -> dict:
    """Load a JSON file, raising a clear ClickException on missing/invalid input."""
    p = Path(path)
    label = what or str(p)
    if not p.is_file():
        raise click.ClickException(f"{label} not found at: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise click.ClickException(f"{label} is not valid JSON ({p}):\n  {e}")
    except OSError as e:
        raise click.ClickException(f"Could not read {label} ({p}): {e}")


def backup_file(path: str | os.PathLike, suffix: str = ".bak") -> Path | None:
    """Copy ``path`` to ``path + suffix`` (preserving content). Returns the
    backup path, or None if the source does not exist."""
    p = Path(path)
    if not p.exists():
        return None
    bak = p.with_name(p.name + suffix)
    bak.write_bytes(p.read_bytes())
    return bak


def atomic_write(
    path: str | os.PathLike, content: str, *, encoding: str = "utf-8"
) -> None:
    """Write ``content`` to ``path`` atomically (temp file in the same dir +
    ``os.replace``), so a failure never leaves a half-written/truncated file.

    Critical for files like the workspace ``.env`` (holds credentials) and
    ``pyproject.toml`` that must never be corrupted.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
