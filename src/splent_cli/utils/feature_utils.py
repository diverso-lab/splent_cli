# Shim — these utils live in splent_framework.utils.feature_utils.
# Kept here for backward compatibility with CLI commands that import from this module.
from splent_framework.utils.feature_utils import get_features_from_pyproject  # noqa: F401


def get_normalize_feature_name_in_splent_format(name: str) -> str:
    """Add the splent_feature_ prefix if not already present."""
    return name if name.startswith("splent_feature_") else f"splent_feature_{name}"


def _read_list(data: dict, key: str) -> list[str]:
    """Read a features list from [tool.splent.<key>], fallback to legacy location."""
    raw = data.get("tool", {}).get("splent", {}).get(key)
    # Legacy fallback only for the base "features" key
    if raw is None and key == "features":
        raw = data.get("project", {}).get("optional-dependencies", {}).get("features", [])
    if not isinstance(raw, (list, type(None))):
        return []
    return [x.strip() for x in (raw or []) if isinstance(x, str) and x.strip()]


def read_features_from_data(data: dict, env: str | None = None) -> list[str]:
    """Extract the merged features list from a parsed pyproject.toml dict.

    Reads from ``[tool.splent.features]`` (always included), then appends
    env-specific entries from ``[tool.splent.features_dev]`` or
    ``[tool.splent.features_prod]`` when ``env`` is provided.

    Args:
        data: Parsed pyproject.toml dict.
        env: ``"dev"``, ``"prod"``, or ``None`` (base features only).

    Returns:
        Deduplicated list of feature entries preserving declaration order.
    """
    base = _read_list(data, "features")

    if env:
        env_key = f"features_{env}"
        env_features = _read_list(data, env_key)
        if env_features:
            # Merge: base first, then env-specific (dedup preserving order)
            seen = set(base)
            for f in env_features:
                if f not in seen:
                    base.append(f)
                    seen.add(f)

    return base


def write_features_to_data(
    data: dict,
    features: list[str],
    key: str = "features",
) -> None:
    """Write a features list into a parsed pyproject.toml dict.

    Writes to ``[tool.splent.<key>]``. When writing the base ``features``
    key, also removes the legacy ``[project.optional-dependencies].features``.

    Args:
        data: Parsed pyproject.toml dict (modified in-place).
        features: Feature entries to write.
        key: ``"features"``, ``"features_dev"``, or ``"features_prod"``.
    """
    data.setdefault("tool", {}).setdefault("splent", {})[key] = features
    # Remove legacy location if present
    if key == "features":
        opt_deps = data.get("project", {}).get("optional-dependencies", {})
        opt_deps.pop("features", None)
