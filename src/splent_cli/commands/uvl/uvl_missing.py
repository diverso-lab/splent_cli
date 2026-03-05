import os
import re
from pathlib import Path

import click
import tomllib

from flamapy.core.discover import DiscoverMetamodels


# -------------------------
# Shared helpers
# -------------------------

def _read_splent_app(workspace: str = "/workspace") -> str:
    env_path = os.path.join(workspace, ".env")
    if not os.path.exists(env_path):
        raise click.ClickException("Missing /workspace/.env (run: splent product:select <app>)")

    app_name = None
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SPLENT_APP="):
                app_name = line.strip().split("=", 1)[1]

    if not app_name:
        raise click.ClickException("SPLENT_APP not set in /workspace/.env (run: splent product:select <app>)")

    product_path = os.path.join(workspace, app_name)
    if not os.path.isdir(product_path):
        raise click.ClickException(f"Active product not found: {product_path}")

    return app_name


def _load_pyproject(pyproject_path: str) -> dict:
    p = Path(pyproject_path)
    if not p.exists():
        raise click.ClickException(f"Missing {pyproject_path}")
    with open(p, "rb") as f:
        return tomllib.load(f)


def _get_uvl_cfg(data: dict) -> dict:
    try:
        return data["tool"]["splent"]["uvl"]
    except KeyError:
        raise click.ClickException("Missing [tool.splent.uvl] in pyproject.toml")


def _get_feature_deps(data: dict) -> list[str]:
    return (
        data.get("project", {})
        .get("optional-dependencies", {})
        .get("features", [])
    )


def _normalize_feature_name(dep: str) -> str:
    s = dep.strip()
    if "/" in s:
        s = s.split("/", 1)[1]
    s = s.split("@", 1)[0]
    if s.startswith("splent_feature_"):
        s = s[len("splent_feature_"):]
    if not s or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", s):
        raise click.ClickException(f"Cannot normalize feature dependency: {dep}")
    return s


def _iter_children(node):
    if hasattr(node, "children") and node.children is not None:
        return list(node.children)
    if hasattr(node, "get_children"):
        return list(node.get_children())
    return []


def _get_root_feature(fm):
    for attr in ("root", "root_feature"):
        if hasattr(fm, attr):
            r = getattr(fm, attr)
            return r() if callable(r) else r
    if hasattr(fm, "get_root"):
        return fm.get_root()
    raise click.ClickException("Cannot access root feature from Flamapy FM model object")


def _list_all_features_from_uvl(uvl_path: str) -> tuple[list[str], str]:
    dm = DiscoverMetamodels()
    fm = dm.use_transformation_t2m(uvl_path, "fm")

    root = _get_root_feature(fm)
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
        stack.extend(_iter_children(n))

    return sorted(names), root_name


# -------------------------
# Missing: parse constraints (implications)
# -------------------------

def _extract_implications_from_uvl_text(uvl_text: str) -> list[tuple[str, str]]:
    """
    Parse lines like:
      confirmemail => mail
      profile => auth
    Supports identifiers only (no dotted names, no parentheses).
    Ignores comments and whitespace.
    """
    pairs = []
    for line in uvl_text.splitlines():
        line = line.strip()

        if not line or line.startswith("//"):
            continue

        # ignore html escaped versions if any show up (your UI prints =&gt;)
        line = line.replace("=&gt;", "=>")

        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=>\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", line)
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


@click.command(
    "uvl:missing",
    short_help="List missing required features according to UVL constraints (based on pyproject selection)",
)
@click.option("--workspace", default="/workspace", show_default=True)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--fail", is_flag=True, help="Exit with code 2 if missing dependencies are found")
def uvl_missing(workspace, pyproject, fail):
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    uvl_cfg = _get_uvl_cfg(data)
    file = uvl_cfg.get("file")
    if not file:
        raise click.ClickException("Missing [tool.splent.uvl].file in pyproject.toml")

    local_uvl = os.path.join(product_path, "uvl", file)
    if not os.path.exists(local_uvl):
        raise click.ClickException(f"UVL not downloaded: {local_uvl} (run: splent uvl:fetch)")

    # universe + root
    universe, root_name = _list_all_features_from_uvl(local_uvl)

    # selection from pyproject
    deps = _get_feature_deps(data)
    selected = {_normalize_feature_name(d) for d in deps}
    selected.add(root_name)  # always selected

    # sanity: selected must exist
    unknown = sorted([f for f in selected if f not in universe])
    if unknown:
        raise click.ClickException(f"pyproject contains features not present in UVL: {', '.join(unknown)}")

    # parse implications from UVL text
    uvl_text = Path(local_uvl).read_text(encoding="utf-8", errors="replace")
    implications = _extract_implications_from_uvl_text(uvl_text)

    # compute missing
    violations = []
    missing_features = set()

    for a, b in implications:
        if a in selected and b not in selected:
            violations.append((a, b))
            missing_features.add(b)

    click.echo()
    click.echo("UVL missing")
    click.echo(f"Product  : {app_name}")
    click.echo(f"UVL      : {local_uvl}")
    click.echo(f"Selected : {', '.join(sorted(selected))}")
    click.echo()

    if not violations:
        click.echo("OK: no missing dependencies (implication constraints satisfied).")
        click.echo()
        return

    click.echo("Missing dependencies detected:")
    for a, b in violations:
        click.echo(f"- {a} requires {b}")

    click.echo()
    click.echo(f"Missing features to add: {', '.join(sorted(missing_features))}")
    click.echo()

    if fail:
        raise click.ClickException("Missing dependencies found")
