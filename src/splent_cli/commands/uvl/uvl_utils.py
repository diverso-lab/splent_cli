"""
Shared helpers for UVL commands.
Centralises the logic that was previously copied verbatim across every uvl_*.py file.
"""

import os
import re
import tempfile
from pathlib import Path

import click
import tomllib

from flamapy.core.discover import DiscoverMetamodels
from splent_cli.utils.feature_utils import read_features_from_data


def read_splent_app(workspace: str) -> str:
    """Read SPLENT_APP from workspace .env and validate the product directory exists."""
    env_path = os.path.join(workspace, ".env")
    if not os.path.exists(env_path):
        raise click.ClickException(
            f"Missing {env_path} (run: splent product:select <app>)"
        )

    app_name = None
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SPLENT_APP="):
                app_name = line.strip().split("=", 1)[1]

    if not app_name:
        raise click.ClickException(
            f"SPLENT_APP not set in {env_path} (run: splent product:select <app>)"
        )

    product_path = os.path.join(workspace, app_name)
    if not os.path.isdir(product_path):
        raise click.ClickException(f"Active product not found: {product_path}")

    return app_name


def load_pyproject(pyproject_path: str) -> dict:
    """Load and parse a pyproject.toml file."""
    p = Path(pyproject_path)
    if not p.exists():
        raise click.ClickException(f"Missing {pyproject_path}")
    with open(p, "rb") as f:
        return tomllib.load(f)


def get_uvl_cfg(data: dict) -> dict:
    """Extract [tool.splent.uvl] section from pyproject data."""
    try:
        return data["tool"]["splent"]["uvl"]
    except KeyError:
        raise click.ClickException("Missing [tool.splent.uvl] in pyproject.toml")


def get_feature_deps(data: dict) -> list[str]:
    """Return the features list from [project.optional-dependencies]."""
    return read_features_from_data(data)


def normalize_feature_name(dep: str) -> str:
    """
    Normalize a feature dependency string to its short name.
    e.g. "splent_io/splent_feature_auth@v1.0.0" -> "auth"
    """
    s = dep.strip()
    if "/" in s:
        s = s.split("/", 1)[1]
    s = s.split("@", 1)[0]
    if s.startswith("splent_feature_"):
        s = s[len("splent_feature_") :]
    if not s or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", s):
        raise click.ClickException(f"Cannot normalize feature dependency: {dep}")
    return s


def resolve_uvlhub_raw_url(mirror: str, doi: str, file: str) -> str:
    """Resolve a uvlhub.io DOI + filename to a raw download URL."""
    if mirror != "uvlhub.io":
        raise click.ClickException(
            f"Unsupported mirror '{mirror}' (only 'uvlhub.io' implemented)"
        )
    return f"https://www.uvlhub.io/doi/{doi}/files/raw/{file}/"


def iter_children(node):
    """Iterate over children of a Flamapy feature model node."""
    if hasattr(node, "children") and node.children is not None:
        return list(node.children)
    if hasattr(node, "get_children"):
        return list(node.get_children())
    return []


def get_root_feature(fm):
    """Get the root feature from a Flamapy FM model."""
    for attr in ("root", "root_feature"):
        if hasattr(fm, attr):
            r = getattr(fm, attr)
            return r() if callable(r) else r
    if hasattr(fm, "get_root"):
        return fm.get_root()
    raise click.ClickException(
        "Cannot access root feature from Flamapy FM model object"
    )


def list_all_features_from_uvl(uvl_path: str) -> tuple[list[str], str]:
    """
    Parse a UVL file and return (sorted_feature_names, root_name).
    """
    dm = DiscoverMetamodels()
    fm = dm.use_transformation_t2m(uvl_path, "fm")

    root = get_root_feature(fm)
    root_name = getattr(root, "name", None)
    if not isinstance(root_name, str) or not root_name:
        raise click.ClickException("Cannot determine root feature name from UVL")

    seen = set()
    names = []
    stack = [root]

    while stack:
        n = stack.pop()
        name = getattr(n, "name", None)
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            names.append(name)
        stack.extend(iter_children(n))

    return sorted(names), root_name


def extract_implications_from_uvl_text(uvl_text: str) -> list[tuple[str, str]]:
    """
    Parse implication constraints (A => B) from UVL text.
    Ignores comments and whitespace.
    """
    pairs = []
    for line in uvl_text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        line = line.replace("=&gt;", "=>")
        m = re.match(
            r"^([A-Za-z_][A-Za-z0-9_]*)\s*=>\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", line
        )
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


def print_uvl_header(
    command: str, app_name: str, local_uvl: str, n_features: int
) -> None:
    """Print the standard UVL command header."""
    click.echo()
    click.echo(f"UVL {command}")
    click.echo(f"Product  : {app_name}")
    click.echo(f"UVL      : {local_uvl}")
    click.echo(f"Features : {n_features}")
    click.echo()


def write_csvconf_full(universe: list[str], selected: set[str]) -> str:
    """
    Write a temporary csvconf file for Flamapy configuration validation.
    Returns the path to the temp file — caller is responsible for deletion.
    """
    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".csvconf", delete=False, encoding="utf-8", newline=""
    )
    try:
        for feat in universe:
            tmp.write(f"{feat},{1 if feat in selected else 0}\n")
        tmp.flush()
        return tmp.name
    finally:
        tmp.close()
