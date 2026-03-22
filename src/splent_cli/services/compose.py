"""
Docker Compose helpers shared across all product commands.
"""
import os
from pathlib import Path


def project_name(name: str, env: str) -> str:
    """Generate a safe Docker Compose project name from product/feature name and env."""
    return f"{name}_{env}".replace("/", "_").replace("@", "_").replace(".", "_")


def resolve_file(product_path: str, env: str) -> str | None:
    """Return the path to the active docker-compose file, or None if not found.

    Prefers docker-compose.{env}.yml, falls back to docker-compose.yml.
    """
    docker_dir = os.path.join(product_path, "docker")
    preferred = os.path.join(docker_dir, f"docker-compose.{env}.yml")
    fallback = os.path.join(docker_dir, "docker-compose.yml")
    if os.path.exists(preferred):
        return preferred
    if os.path.exists(fallback):
        return fallback
    return None


def feature_docker_dir(workspace: str, feature: str) -> str:
    """Return the docker/ directory for a feature entry in the cache."""
    return os.path.join(workspace, ".splent_cache", "features", feature, "docker")


def normalize_feature_ref(feat: str) -> str:
    """Normalise a raw feature ref to org_safe/name format.

    'features/splent_io/splent_feature_auth' -> 'splent_io/splent_feature_auth'
    'splent_feature_auth'                    -> 'splent_io/splent_feature_auth'
    'splent_io/splent_feature_auth'          -> 'splent_io/splent_feature_auth'
    """
    if "features/" in feat:
        feat = feat.split("features/")[-1]
    if "/" not in feat:
        feat = f"splent_io/{feat}"
    return feat


def product_path(product: str, workspace: str) -> str:
    """Return the absolute path to a product directory."""
    return os.path.join(workspace, product)


def parse_feature_identifier(identifier: str) -> tuple[str, str, str, str]:
    """Parse a feature identifier into its components.

    Accepts two forms:
      - "namespace/feature_name"   → explicit namespace
      - "feature_name"             → defaults to "splent-io"

    Returns (namespace, namespace_github, namespace_fs, feature_name) where:
      namespace_github  dash-separated  (GitHub org name)
      namespace_fs      underscore-separated (filesystem safe)
    """
    if "/" in identifier:
        namespace, feature_name = identifier.split("/", 1)
    else:
        namespace = "splent-io"
        feature_name = identifier

    namespace_github = namespace.replace("_", "-")
    namespace_fs = namespace.replace("-", "_").replace(".", "_")

    return namespace, namespace_github, namespace_fs, feature_name


def remove_broken_symlinks(workspace: Path) -> int:
    """Remove broken feature symlinks from all products under workspace.

    Returns the number of symlinks removed.
    """
    removed = 0
    for product_dir in workspace.iterdir():
        features_dir = product_dir / "features"
        if not features_dir.is_dir():
            continue
        for org_dir in features_dir.iterdir():
            if not org_dir.is_dir():
                continue
            for link in org_dir.iterdir():
                if link.is_symlink() and not link.exists():
                    link.unlink()
                    removed += 1
    return removed
