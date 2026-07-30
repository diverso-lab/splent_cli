# Shim — these utils live in splent_framework.utils.feature_utils.
# Kept here for backward compatibility with CLI commands that import from this module.
from splent_framework.utils.feature_utils import get_features_from_pyproject  # noqa: F401

import os

from splent_cli.utils.proc import run


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
        click.secho(
            f"    no compose file for '{product}' (env={env}); "
            "skipping hot reinstall in web container.",
            fg="yellow",
        )
        return

    pname = compose.project_name(product, env)
    container_id = compose.find_main_container(pname, compose_file, docker_dir)
    if not container_id:
        click.secho(
            f"    web container for '{product}' (env={env}) is not running; "
            "skipping hot reinstall.",
            fg="yellow",
        )
        return

    click.echo(click.style("    reinstalling in web container...", dim=True))
    pip_cmd = (
        f"pip install --no-cache-dir --root-user-action=ignore -q -e {install_path}"
    )
    result = run(
        ["docker", "exec", container_id, "bash", "-c", pip_cmd],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        msg = f"    pip install of '{name}' failed in web container (exit {result.returncode})."
        if detail:
            msg += f"\n{detail}"
        click.secho(msg, fg="red")
        raise SystemExit(1)

    # Touch the app's __init__.py to trigger watchmedo auto-restart
    init_py = f"/workspace/{product}/src/{product}/__init__.py"
    touch = run(
        ["docker", "exec", container_id, "bash", "-c", f"touch {init_py}"],
        check=False,
        capture=True,
    )
    if touch.returncode != 0:
        detail = (touch.stderr or touch.stdout or "").strip()
        msg = (
            "    could not touch __init__.py to trigger reload "
            f"(exit {touch.returncode}); restart the web container manually."
        )
        if detail:
            msg += f"\n{detail}"
        click.secho(msg, fg="yellow")


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
        click.secho(
            f"    no compose file for '{product}' (env={env}); "
            "skipping hot uninstall from web container.",
            fg="yellow",
        )
        return

    pname = compose.project_name(product, env)
    container_id = compose.find_main_container(pname, compose_file, docker_dir)
    if not container_id:
        click.secho(
            f"    web container for '{product}' (env={env}) is not running; "
            "skipping hot uninstall.",
            fg="yellow",
        )
        return

    click.echo(click.style("    uninstalling from web container...", dim=True))
    pip_cmd = f"pip uninstall -y -q {name}"
    result = run(
        ["docker", "exec", container_id, "bash", "-c", pip_cmd],
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        msg = f"    pip uninstall of '{name}' failed in web container (exit {result.returncode})."
        if detail:
            msg += f"\n{detail}"
        click.secho(msg, fg="red")
        raise SystemExit(1)

    # Touch the app's __init__.py to trigger watchmedo auto-restart
    init_py = f"/workspace/{product}/src/{product}/__init__.py"
    touch = run(
        ["docker", "exec", container_id, "bash", "-c", f"touch {init_py}"],
        check=False,
        capture=True,
    )
    if touch.returncode != 0:
        detail = (touch.stderr or touch.stdout or "").strip()
        msg = (
            "    could not touch __init__.py to trigger reload "
            f"(exit {touch.returncode}); restart the web container manually."
        )
        if detail:
            msg += f"\n{detail}"
        click.secho(msg, fg="yellow")


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


# Every list a feature entry may be declared in, in declaration order.
FEATURE_LIST_KEYS = ("features", "features_dev", "features_prod")


def read_feature_list(data: dict, key: str = "features") -> list[str]:
    """Return a single feature list from a parsed pyproject dict (no env merge).

    Unlike read_features_from_data(), this never merges the base list with an
    env-specific one — callers that edit one list need to see it in isolation.
    """
    return _read_list(data, key)


def entry_matches_feature(entry: str, name: str, namespace: str | None = None) -> bool:
    """Return True when *entry* declares the feature (namespace, name).

    The namespace spelling is normalized on both sides, so ``splent-io`` (the
    GitHub org) and ``splent_io`` (the Python namespace) are the same namespace.
    The pinned version is ignored, so ``x``, ``ns/x`` and ``ns/x@v1`` all match
    the same feature.  Pass ``namespace=None`` to match on the name alone.
    """
    entry_ns, entry_name, _ = parse_feature_entry(entry)
    if entry_name != name:
        return False
    if namespace is None:
        return True
    return entry_ns == normalize_namespace(namespace)


def find_feature_entries(
    data: dict,
    name: str,
    namespace: str | None = None,
    keys: tuple[str, ...] = FEATURE_LIST_KEYS,
) -> list[tuple[str, str]]:
    """Return every declaration of a feature as ``(features_key, entry)`` pairs.

    A feature is expected to be declared exactly once; more than one pair means
    the product declares it twice (e.g. once per namespace spelling).
    """
    found: list[tuple[str, str]] = []
    for key in keys:
        for entry in _read_list(data, key):
            if entry_matches_feature(entry, name, namespace):
                found.append((key, entry))
    return found


def drop_feature_entries(
    data: dict,
    name: str,
    namespace: str | None = None,
    keys: tuple[str, ...] = FEATURE_LIST_KEYS,
) -> list[tuple[str, str]]:
    """Remove every declaration of a feature from *data* (modified in-place).

    Returns the removed ``(features_key, entry)`` pairs so the caller can also
    clean up whatever each declaration created (symlink, manifest entry).
    """
    removed: list[tuple[str, str]] = []
    for key in keys:
        entries = _read_list(data, key)
        kept = [e for e in entries if not entry_matches_feature(e, name, namespace)]
        if kept != entries:
            removed.extend((key, e) for e in entries if e not in kept)
            write_features_to_data(data, kept, key=key)
    return removed


def remove_feature_link(
    product_path: str,
    namespace: str,
    name: str,
    version: str | None = None,
) -> bool:
    """Remove the product symlink created for one feature declaration.

    Returns True when a symlink was removed.  Real directories are left alone —
    they are not ours to delete.
    """
    leaf = f"{name}@{version}" if version else name
    link_path = os.path.join(
        product_path, "features", normalize_namespace(namespace), leaf
    )
    if os.path.islink(link_path) or os.path.isfile(link_path):
        os.unlink(link_path)
        return True
    return False


def product_namespace_spelling(data: dict, namespace: str, default: str) -> str:
    """How this product already spells that namespace, or ``default``.

    ``splent-io`` and ``splent_io`` name the same namespace, one being the
    hosting org and the other the Python package, and both are accepted
    everywhere. A product that carries the two spellings at once reads as if
    it depended on two different orgs, and product:validate says so. Since the
    spelling comes from whatever the person typed, a command that adds an
    entry should adopt what is already there rather than create the condition
    the CLI then warns about.

    A product that already mixes them gets its majority spelling, so
    re-attaching converges on the one it mostly uses instead of on whichever
    happens to be declared first. Ties keep the earlier one.
    """
    target = normalize_namespace(namespace)
    counts: dict[str, int] = {}
    for key in FEATURE_LIST_KEYS:
        for entry in read_feature_list(data, key):
            if "/" not in entry:
                continue
            spelling = entry.split("/")[0]
            if normalize_namespace(spelling) == target:
                counts[spelling] = counts.get(spelling, 0) + 1
    if not counts:
        return default
    return max(counts, key=lambda spelling: counts[spelling])


def prune_feature_links(
    product_path: str,
    namespace: str,
    name: str,
    keep: str | None = None,
) -> list[str]:
    """Leave one symlink for a feature and remove the rest.

    A product runs one version of a feature, so two links for the same one
    put two copies of the same package on the path and which wins is down to
    directory order.  Removing what the previous declaration created is not
    enough to guarantee that: a link whose version is no longer declared
    anywhere, left behind by an interrupted command or by hand-editing, is
    invisible to that bookkeeping and survives every later attach.

    ``keep`` is the leaf to preserve, normally ``name@version`` for a pinned
    feature or ``name`` for an editable one.  Real directories are left
    alone, they are not ours to delete.  Returns the leaves removed.
    """
    links_dir = os.path.join(product_path, "features", normalize_namespace(namespace))
    if not os.path.isdir(links_dir):
        return []

    removed = []
    for leaf in sorted(os.listdir(links_dir)):
        if leaf == keep:
            continue
        if leaf != name and not leaf.startswith(f"{name}@"):
            continue
        path = os.path.join(links_dir, leaf)
        if os.path.islink(path):
            os.unlink(path)
            removed.append(leaf)
    return removed


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
