"""
Product feature manifest — splent.manifest.json

Tracks the known lifecycle state of every feature attached to a product.
Updated automatically by CLI commands (feature:add, feature:attach,
feature:remove, feature:detach) and optionally by startup scripts.

State machine
─────────────

    (absent)
       │  feature:create / feature:clone / feature:fork
       ▼
    [cached]
       │  feature:add / feature:attach
       ▼
  [declared]  ◄──── feature:remove / feature:detach
       │  pip install / product:sync
       ▼
  [installed]  ◄──── db:rollback
       │  db:upgrade / db:migrate
       ▼
  [migrated]
       │  Flask startup (FeatureManager.register_features)
       ▼
    [active]

Optional:
  [active] → [disabled]   (feature disabled at runtime but not removed)
  [disabled] → [active]   (re-enabled)
"""

import json
import tomllib
import os
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_FILENAME = "splent.manifest.json"
SCHEMA_VERSION = "1"

# Ordered list of states for progress display
STATES = ["declared", "installed", "migrated", "active"]
VALID_STATES = {"declared", "installed", "migrated", "active", "disabled"}

STATE_COLORS = {
    "declared": "yellow",
    "installed": "cyan",
    "migrated": "blue",
    "active": "green",
    "disabled": "bright_black",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(product_path: str) -> Path:
    return Path(product_path) / MANIFEST_FILENAME


def _load(product_path: str) -> dict:
    p = _path(product_path)
    if not p.exists():
        return {"schema_version": SCHEMA_VERSION, "features": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save(product_path: str, product_name: str, data: dict) -> None:
    data["product"] = product_name
    data["schema_version"] = SCHEMA_VERSION
    data["updated_at"] = _now()
    with open(_path(product_path), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def feature_key(namespace: str, name: str, version: str | None = None) -> str:
    """Build the canonical manifest key for a feature."""
    ns = namespace.replace("-", "_")
    return f"{ns}/{name}@{version}" if version else f"{ns}/{name}"


def set_feature_state(
    product_path: str,
    product_name: str,
    key: str,
    state: str,
    *,
    namespace: str,
    name: str,
    version: str | None = None,
    mode: str = "editable",
) -> None:
    """
    Insert or update a feature entry in splent.manifest.json.
    Timestamps for previous states are preserved across transitions.
    """
    if state not in VALID_STATES:
        raise ValueError(f"Unknown state '{state}'. Must be one of: {VALID_STATES}")

    data = _load(product_path)
    now = _now()
    existing = data["features"].get(key, {})

    entry: dict = {
        "namespace": namespace.replace("-", "_"),
        "name": name,
        "version": version,
        "mode": mode,
        "state": state,
        "declared_at": existing.get("declared_at", now),
        "installed_at": existing.get("installed_at"),
        "migrated_at": existing.get("migrated_at"),
        "updated_at": now,
    }

    if state == "installed":
        entry["installed_at"] = existing.get("installed_at") or now
    elif state == "migrated":
        entry["installed_at"] = existing.get("installed_at") or now
        entry["migrated_at"] = existing.get("migrated_at") or now
    elif state == "active":
        entry["installed_at"] = existing.get("installed_at") or now
        entry["migrated_at"] = existing.get("migrated_at")

    data["features"][key] = entry
    _save(product_path, product_name, data)


def remove_feature(product_path: str, product_name: str, key: str) -> None:
    """Remove a feature entry from splent.manifest.json."""
    data = _load(product_path)
    data["features"].pop(key, None)
    _save(product_path, product_name, data)


def read_manifest(product_path: str) -> dict:
    """Return the full manifest dict (empty scaffold if file does not exist)."""
    return _load(product_path)


def manifest_exists(product_path: str) -> bool:
    return _path(product_path).exists()


def get_dependents(product_path: str, feature_name: str) -> list[str]:
    """
    Return the names of features currently installed in this product that
    declare `feature_name` in their [tool.splent.contract.requires].features.

    Uses the manifest to discover installed features and their namespaces,
    then reads each feature's pyproject.toml via the symlink in features/.
    """
    manifest = _load(product_path)
    features_dir = os.path.join(product_path, "features")
    dependents: list[str] = []

    for entry in manifest.get("features", {}).values():
        name = entry.get("name", "")
        namespace = entry.get("namespace", "")
        if name == feature_name:
            continue

        pyproject_path = os.path.join(features_dir, namespace, name, "pyproject.toml")
        if not os.path.exists(pyproject_path):
            continue

        try:
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            requires = (
                data.get("tool", {})
                .get("splent", {})
                .get("contract", {})
                .get("requires", {})
                .get("features", [])
            )
            if feature_name in requires:
                dependents.append(name)
        except Exception:
            continue

    return dependents


def get_feature_state(product_path: str, key: str) -> str | None:
    """Return the current state of a feature from the manifest, or None if not tracked."""
    data = _load(product_path)
    entry = data.get("features", {}).get(key)
    return entry.get("state") if entry else None
