
import os
from pathlib import Path

from splent_cli.services import context


MARKETPLACE_TOKEN_VAR = "SPLENT_API_TOKEN"
MARKETPLACE_API_URL_VAR = "SPLENT_API_URL"


def _workspace_env_path() -> Path:
    try:
        return context.workspace() / ".env"
    except SystemExit:
        return Path.cwd() / ".env"


def _read_env_lines() -> list[str]:
    env_path = _workspace_env_path()
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8").splitlines(keepends=True)


def _write_env_lines(lines: list[str]) -> None:
    env_path = _workspace_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("".join(lines), encoding="utf-8")


def set_env_var(key: str, value: str) -> None:
    lines = _read_env_lines()
    prefix = f"{key}="
    found = False
    updated = []

    for line in lines:
        if line.startswith(prefix):
            updated.append(f"{key}={value}\n")
            found = True
        else:
            updated.append(line)

    if not found:
        updated.append(f"{key}={value}\n")

    _write_env_lines(updated)
    os.environ[key] = value


def unset_env_var(key: str) -> None:
    lines = _read_env_lines()
    prefix = f"{key}="
    updated = [line for line in lines if not line.startswith(prefix)]
    _write_env_lines(updated)
    os.environ.pop(key, None)
