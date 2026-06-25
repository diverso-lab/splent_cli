"""
Subprocess helpers that turn the two most common "happy-path" failures into
actionable messages instead of raw tracebacks:

  * the tool is not installed / not on PATH  (FileNotFoundError)
  * the tool ran but failed                  (non-zero exit code)

Use these everywhere the CLI shells out to git / docker / pip / mysql / pytest
/ ruff / etc. so a developer on a fresh machine sees *what* to fix.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Sequence

import click


def _tool_of(cmd) -> str:
    if isinstance(cmd, (list, tuple)) and cmd:
        return str(cmd[0])
    return str(cmd).split()[0] if str(cmd).strip() else "command"


def require_tool(name: str, install_hint: str | None = None) -> None:
    """Abort with a clear message if ``name`` is not on PATH."""
    if shutil.which(name) is None:
        msg = f"'{name}' is required but was not found on PATH."
        if install_hint:
            msg += f"\n{install_hint}"
        raise click.ClickException(msg)


def require_docker() -> None:
    """Ensure docker is installed AND the daemon is reachable (without sudo)."""
    require_tool(
        "docker",
        "Install Docker Desktop or Docker Engine: https://docs.docker.com/get-docker/",
    )
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired:
        raise click.ClickException("Docker daemon did not respond (timed out).")
    if result.returncode != 0:
        err = (result.stderr or "").lower()
        if "permission denied" in err or "connect: permission" in err:
            raise click.ClickException(
                "Docker is installed but not reachable without sudo (permission denied).\n"
                "Add your user to the 'docker' group, then re-login:\n"
                "  sudo usermod -aG docker $USER && newgrp docker"
            )
        raise click.ClickException(
            "Docker daemon is not running. Start Docker and try again."
        )


def run(
    cmd: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    text: bool = True,
    timeout: float | None = None,
    tool_hint: str | None = None,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Run a subprocess with friendly error translation.

    Parameters mirror ``subprocess.run`` but:
      * ``capture`` is a shorthand for ``capture_output``.
      * ``FileNotFoundError`` (tool missing) becomes a ``ClickException``
        naming the tool, with an optional ``tool_hint``.
      * a non-zero exit (when ``check=True``) becomes a ``ClickException`` that
        includes the captured stderr/stdout when available.

    Returns the ``CompletedProcess`` so callers can still inspect
    ``returncode`` / ``stdout`` when ``check=False``.
    """
    tool = _tool_of(cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=text,
            timeout=timeout,
            **kwargs,
        )
    except FileNotFoundError:
        hint = tool_hint or f"Install '{tool}' and make sure it is on your PATH."
        raise click.ClickException(f"'{tool}' is not installed or not on PATH.\n{hint}")
    except subprocess.TimeoutExpired:
        raise click.ClickException(
            f"'{tool}' timed out after {timeout}s and was aborted."
        )

    if check and result.returncode != 0:
        detail = ""
        if capture:
            err = (result.stderr or result.stdout or "").strip()
            if err:
                detail = f"\n{err}"
        raise click.ClickException(
            f"'{tool}' failed (exit {result.returncode})." + detail
        )
    return result
