# Shim — these utils live in splent_framework.utils.feature_utils.
# Kept here for backward compatibility with CLI commands that import from this module.
from splent_framework.utils.feature_utils import get_features_from_pyproject  # noqa: F401

import os
import subprocess


def hot_reinstall(product_path: str, install_path: str, name: str):
    """Reinstall a feature via pip in the web container and trigger Flask reload.

    Parameters
    ----------
    product_path : str
        Absolute path to the product directory.
    install_path : str
        Container path to pip install -e from (e.g. /workspace/splent_feature_auth).
    name : str
        Feature package name (for logging).
    """
    import click
    from splent_cli.services import compose

    product = os.path.basename(product_path)
    env = os.getenv("SPLENT_ENV", "dev")
    docker_dir = os.path.join(product_path, "docker")

    compose_file = compose.resolve_file(product_path, env)
    if not compose_file:
        return

    pname = compose.project_name(product, env)
    container_id = compose.find_main_container(pname, compose_file, docker_dir)
    if not container_id:
        return

    click.echo(click.style("    reinstalling in web container...", dim=True))
    pip_cmd = (
        f"pip install --no-cache-dir --root-user-action=ignore -q -e {install_path}"
    )
    subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", pip_cmd],
        capture_output=True,
    )

    # Touch the app's __init__.py to trigger watchmedo auto-restart
    init_py = f"/workspace/{product}/src/{product}/__init__.py"
    subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", f"touch {init_py}"],
        capture_output=True,
    )


def hot_uninstall(product_path: str, name: str):
    """Uninstall a feature via pip in the web container and trigger Flask reload.

    Parameters
    ----------
    product_path : str
        Absolute path to the product directory.
    name : str
        Feature package name (e.g. splent_feature_auth).
    """
    import click
    from splent_cli.services import compose

    product = os.path.basename(product_path)
    env = os.getenv("SPLENT_ENV", "dev")
    docker_dir = os.path.join(product_path, "docker")

    compose_file = compose.resolve_file(product_path, env)
    if not compose_file:
        return

    pname = compose.project_name(product, env)
    container_id = compose.find_main_container(pname, compose_file, docker_dir)
    if not container_id:
        return

    click.echo(click.style("    uninstalling from web container...", dim=True))
    pip_cmd = f"pip uninstall -y -q {name}"
    subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", pip_cmd],
        capture_output=True,
    )

    # Touch the app's __init__.py to trigger watchmedo auto-restart
    init_py = f"/workspace/{product}/src/{product}/__init__.py"
    subprocess.run(
        ["docker", "exec", container_id, "bash", "-c", f"touch {init_py}"],
        capture_output=True,
    )


DEFAULT_NAMESPACE = "splent-io"


def get_normalize_feature_name_in_splent_format(name: str) -> str:
    """Add the splent_feature_ prefix if not already present."""
    return name if name.startswith("splent_feature_") else f"splent_feature_{name}"


def normalize_namespace(ns: str) -> str:
    """Normalize a namespace to a filesystem-safe Python identifier.

    ``"splent-io"`` → ``"splent_io"``
    ``"my.org"``    → ``"my_org"``
    """
    return ns.replace("-", "_").replace(".", "_")


def parse_feature_entry(entry: str) -> tuple[str, str, str | None]:
    """Parse a feature entry from pyproject.toml into (namespace_safe, name, version | None).

    Accepted formats::

        splent-io/splent_feature_auth@v1.2.7  →  ("splent_io", "splent_feature_auth", "v1.2.7")
        splent-io/splent_feature_auth         →  ("splent_io", "splent_feature_auth", None)
        splent_feature_auth@v1.2.7            →  ("splent_io", "splent_feature_auth", "v1.2.7")
        splent_feature_auth                   →  ("splent_io", "splent_feature_auth", None)
    """
    base, _, version = entry.partition("@")
    if "/" in base:
        ns_raw, name = base.split("/", 1)
    else:
        ns_raw = DEFAULT_NAMESPACE
        name = base
    return normalize_namespace(ns_raw), name, version or None


def load_product_pyproject(product_dir: str) -> dict:
    """Load and return the parsed pyproject.toml dict for a product directory.

    Raises FileNotFoundError if the file does not exist.
    """
    import os
    import tomllib

    path = os.path.join(product_dir, "pyproject.toml")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"pyproject.toml not found: {path}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_product_features(product_dir: str, env: str | None = None) -> list[str]:
    """Read and return the merged feature list from a product's pyproject.toml.

    Convenience wrapper around load_product_pyproject + read_features_from_data.
    """
    data = load_product_pyproject(product_dir)
    return read_features_from_data(data, env)


def _read_list(data: dict, key: str) -> list[str]:
    """Read a features list from [tool.splent.<key>], fallback to legacy location."""
    raw = data.get("tool", {}).get("splent", {}).get(key)
    # Legacy fallback only for the base "features" key
    if raw is None and key == "features":
        raw = (
            data.get("project", {}).get("optional-dependencies", {}).get("features", [])
        )
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
