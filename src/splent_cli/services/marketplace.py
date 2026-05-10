import os
from pathlib import Path

import requests

from splent_cli.services import context
from splent_cli.services.env import cli_env_path


MARKETPLACE_TOKEN_VAR = "SPLENT_API_TOKEN"
MARKETPLACE_API_URL_VAR = "SPLENT_API_URL"
MARKETPLACE_AUTH_VAR = "SPLENT_MARKETPLACE_AUTH"
DEFAULT_API_URL = "https://api.splent.io"


class MarketplaceLoginError(RuntimeError):
    pass


def _workspace_env_path() -> Path:
    env_path = cli_env_path()
    if env_path.exists():
        return env_path

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


def clear_env_var(key: str) -> None:
    set_env_var(key, '""')
    os.environ.pop(key, None)


def token_value() -> str:
    return (os.getenv(MARKETPLACE_TOKEN_VAR) or "").strip().strip("\"'")


def is_logged_in() -> bool:
    return os.getenv(MARKETPLACE_AUTH_VAR) == "true" and bool(token_value())


def validate_api_token(api_url: str, token: str | None = None) -> bool:
    url = api_url.rstrip("/")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.get(
            f"{url}/api/auth/check",
            headers=headers,
            timeout=10,
        )
    except requests.exceptions.RequestException as exc:
        raise MarketplaceLoginError(
            f"Could not connect to the SPLENT API: {exc}"
        ) from exc

    if response.status_code == 200:
        return True

    if response.status_code in {401, 403}:
        return False

    raise MarketplaceLoginError(
        f"SPLENT API returned HTTP {response.status_code}."
    )
