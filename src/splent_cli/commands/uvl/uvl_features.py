import os
from pathlib import Path

import click
import tomllib

from flamapy.core.discover import DiscoverMetamodels


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


@click.command(
    "uvl:features",
    short_help="Print the list of features present in the downloaded UVL model",
)
@click.option("--workspace", default="/workspace", show_default=True)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--no-root", is_flag=True, help="Do not print the root feature")
def uvl_features(workspace, pyproject, no_root):
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

    feats, root_name = _list_all_features_from_uvl(local_uvl)

    click.echo()
    click.echo(f"UVL features ({len(feats)})")
    click.echo(f"Product: {app_name}")
    click.echo(f"UVL: {local_uvl}")
    click.echo()

    for f in feats:
        if no_root and f == root_name:
            continue
        click.echo(f"- {f}")

    click.echo()
