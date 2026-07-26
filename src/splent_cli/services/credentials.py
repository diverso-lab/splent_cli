"""Credential store for the SPLENT marketplace, keyed by registry URL.

Where it lives, and why
-----------------------
The CLI normally runs INSIDE ``splent_cli_container``. That container's home
directory is part of the image: it is recreated empty on every rebuild, so a
token written to ``~/.splent`` disappears the first time somebody runs
``make setup-rebuild``. The only path that is reliably mounted and survives a
rebuild is the workspace itself (``../../:/workspace`` in the CLI compose
file), and it is already there for every developer with no extra volume to
declare. Asking somebody to edit a compose file before they can log in is not
an acceptable onboarding step, so the default store is::

    <WORKING_DIR>/.splent/credentials.json      (0600, in a 0700 directory)

That survives ``product:restart``, a container rebuild and ``make setup``,
because none of them touch the workspace. It is deliberately NOT
``.splent_cache/`` (a regenerable cache that commands are free to wipe) and
deliberately NOT the workspace ``.env`` (a tracked-by-hand file that is edited
by tooling and read by Docker, and which this project forbids writing to).

Overrides and fallbacks
-----------------------
``SPLENT_CREDENTIALS`` overrides the path completely, which is how tests and
throwaway environments avoid touching a real store. When no workspace is
visible at all (the CLI installed from PyPI and run outside a workspace) the
store falls back to ``~/.splent/credentials.json``; ``store_origin()`` reports
which of the three applies so ``whoami`` can show it.

Contents
--------
One entry per registry URL, so production and a local marketplace coexist::

    {
      "schema": 1,
      "registries": {
        "https://marketplace.splent.io": {
          "token": "...",
          "identity": "dev@example.com",
          "token_name": "splent-cli@laptop",
          "scopes": ["spl:publish"],
          "expires_at": "2026-10-24T10:00:00+00:00",
          "updated_at": "2026-07-26T12:00:00+00:00"
        }
      }
    }

The token is never printed, never logged and never passed on a command line.
The cached identity fields exist so ``whoami`` can still tell the developer who
they are when the marketplace is unreachable.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from splent_cli.services.marketplace_url import (
    InvalidRegistryURL,
    normalize_registry_url,
)

SCHEMA = 1

#: Overrides the store path completely (used by tests and CI).
CREDENTIALS_ENV = "SPLENT_CREDENTIALS"

#: A token supplied by the environment. Wins over the stored one, the way CI
#: expects, and ``whoami`` says so explicitly.
TOKEN_ENV = "SPLENT_MARKETPLACE_TOKEN"

STORE_DIRNAME = ".splent"
STORE_FILENAME = "credentials.json"

_DIR_MODE = 0o700
_FILE_MODE = 0o600


class CredentialsError(Exception):
    """The credential store could not be read or written.

    Always carries an actionable message: an unwritable store must say so and
    say what to do, never fail silently or half-write.
    """


@dataclass
class Credential:
    """A token plus where it came from (needed to explain CI vs laptop)."""

    token: str
    source: str  # "environment" or "file"
    source_label: str  # the env var name, or the store path
    entry: dict = field(default_factory=dict)

    @property
    def from_environment(self) -> bool:
        return self.source == "environment"


# ── Where the store lives ─────────────────────────────────────────────


def _workspace_dir() -> Path | None:
    """The mounted workspace, or None when it is not visible from here.

    Deliberately does not use ``services.context.workspace()``: that one exits
    the process when the workspace is missing, and resolving a credential path
    must never kill a command that could still work.
    """
    raw = os.getenv("WORKING_DIR") or "/workspace"
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def store_origin() -> str:
    """``"env"``, ``"workspace"`` or ``"home"`` for the active store path."""
    if os.getenv(CREDENTIALS_ENV):
        return "env"
    return "workspace" if _workspace_dir() is not None else "home"


def store_path() -> Path:
    """Absolute path of the credential store, per the precedence documented above."""
    override = os.getenv(CREDENTIALS_ENV)
    if override:
        return Path(override).expanduser()
    workspace = _workspace_dir()
    if workspace is not None:
        return workspace / STORE_DIRNAME / STORE_FILENAME
    return Path.home() / STORE_DIRNAME / STORE_FILENAME


def _not_writable_message(path: Path, reason: str) -> str:
    lines = [
        f"Could not write the credential store at {path} ({reason}).",
        "The store must sit on a writable path that survives a container rebuild.",
        f"Point {CREDENTIALS_ENV} at a writable file and log in again, for example",
        f"  export {CREDENTIALS_ENV}=$WORKING_DIR/.splent/credentials.json",
    ]
    if store_origin() == "home":
        lines.append(
            "Note that a path under the container home directory is lost on "
            "the next rebuild."
        )
    return "\n".join(lines)


# ── Reading ───────────────────────────────────────────────────────────


def _key(registry_url: str) -> str:
    """Normalise the registry URL so one registry never gets two entries."""
    try:
        return normalize_registry_url(registry_url)
    except InvalidRegistryURL:
        return str(registry_url).strip()


def load_store() -> dict:
    """Parse the store. Returns an empty document when the file does not exist."""
    path = store_path()
    if not path.exists():
        return {"schema": SCHEMA, "registries": {}}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CredentialsError(
            f"Could not read the credential store at {path} ({e.strerror or e}). "
            f"Check the file permissions, or set {CREDENTIALS_ENV} to another path."
        )
    if not raw.strip():
        return {"schema": SCHEMA, "registries": {}}
    try:
        data = json.loads(raw)
    except ValueError:
        raise CredentialsError(
            f"The credential store at {path} is not valid JSON. "
            "Delete the file and run splent login again."
        )
    if not isinstance(data, dict) or not isinstance(data.get("registries"), dict):
        raise CredentialsError(
            f"The credential store at {path} has an unexpected layout. "
            "Delete the file and run splent login again."
        )
    return data


def get(registry_url: str) -> dict | None:
    """The stored entry for *registry_url*, or None."""
    entry = load_store()["registries"].get(_key(registry_url))
    return entry if isinstance(entry, dict) else None


def resolve(registry_url: str) -> Credential | None:
    """The token to use for *registry_url*, with its provenance.

    ``SPLENT_MARKETPLACE_TOKEN`` wins over the file, which is what CI expects
    and what makes a pipeline reproducible. ``whoami`` reports the winner so a
    developer can see why their laptop and CI disagree.
    """
    env_token = (os.getenv(TOKEN_ENV) or "").strip()
    if env_token:
        # No cached identity is attached on purpose: whatever the file holds
        # describes a DIFFERENT token, and showing it here would answer
        # "who am I" with somebody else's answer.
        return Credential(
            token=env_token,
            source="environment",
            source_label=TOKEN_ENV,
            entry={},
        )

    entry = get(registry_url)
    if not entry:
        return None
    token = (entry.get("token") or "").strip()
    if not token:
        return None
    public = {k: v for k, v in entry.items() if k != "token"}
    return Credential(
        token=token,
        source="file",
        source_label=str(store_path()),
        entry=public,
    )


# ── Writing ───────────────────────────────────────────────────────────


def _ensure_dir(path: Path) -> None:
    directory = path.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise CredentialsError(_not_writable_message(path, e.strerror or str(e)))
    try:
        os.chmod(directory, _DIR_MODE)
    except OSError:
        # A mount that refuses chmod is still usable; the file mode is what
        # protects the token, and that one is enforced below.
        pass
    # Belt and braces: the workspace root is not a git repository, but the
    # store must never become one's tracked content by accident.
    marker = directory / ".gitignore"
    if not marker.exists():
        try:
            marker.write_text("*\n", encoding="utf-8")
        except OSError:
            pass


def _write_store(data: dict) -> Path:
    path = store_path()
    _ensure_dir(path)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    try:
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
    except OSError as e:
        raise CredentialsError(_not_writable_message(path, e.strerror or str(e)))
    try:
        os.fchmod(fd, _FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise CredentialsError(_not_writable_message(path, e.strerror or str(e)))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, _FILE_MODE)
    except OSError:
        pass
    return path


def save(
    registry_url: str,
    *,
    token: str,
    identity: str | None = None,
    token_name: str | None = None,
    scopes: list[str] | None = None,
    expires_at: str | None = None,
) -> Path:
    """Store *token* for *registry_url* and return the store path.

    Other registries in the file are preserved: production and a local
    marketplace are independent entries.
    """
    if not token or not str(token).strip():
        raise CredentialsError("Refusing to store an empty token.")

    data = load_store()
    data["schema"] = SCHEMA
    registries = data.setdefault("registries", {})
    entry = {
        "token": str(token).strip(),
        "identity": identity,
        "token_name": token_name,
        "scopes": list(scopes) if scopes else [],
        "expires_at": expires_at,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    registries[_key(registry_url)] = entry
    return _write_store(data)


def delete(registry_url: str) -> bool:
    """Remove the entry for *registry_url*. True when something was removed."""
    try:
        data = load_store()
    except CredentialsError:
        # A store we cannot parse is a store we cannot trust; a logout must
        # still guarantee the local delete, so start it over from scratch.
        path = store_path()
        if path.exists():
            _write_store({"schema": SCHEMA, "registries": {}})
            return True
        return False

    registries = data.get("registries", {})
    if _key(registry_url) not in registries:
        return False
    registries.pop(_key(registry_url))
    _write_store(data)
    return True


def registries() -> list[str]:
    """Registry URLs that currently have a stored token."""
    return sorted(load_store().get("registries", {}))
