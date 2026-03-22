# Shim — these utils live in splent_framework.utils.feature_utils.
# Kept here for backward compatibility with CLI commands that import from this module.
from splent_framework.utils.feature_utils import get_features_from_pyproject  # noqa: F401


def get_normalize_feature_name_in_splent_format(name: str) -> str:
    """Add the splent_feature_ prefix if not already present."""
    return name if name.startswith("splent_feature_") else f"splent_feature_{name}"
