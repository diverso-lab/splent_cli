import os
import re
import sys
import tempfile
from pathlib import Path

import click
import tomllib

from flamapy.core.discover import DiscoverMetamodels
from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel


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


def _resolve_uvlhub_raw_url(mirror: str, doi: str, file: str) -> str:
    if mirror != "uvlhub.io":
        raise click.ClickException(f"Unsupported mirror '{mirror}' (only 'uvlhub.io' implemented)")
    return f"https://www.uvlhub.io/doi/{doi}/files/raw/{file}/"


def _get_feature_deps(data: dict) -> list[str]:
    return (
        data.get("project", {})
        .get("optional-dependencies", {})
        .get("features", [])
    )


def _normalize_feature_name(dep: str) -> str:
    """
    Accept:
      splent_feature_auth@v1.0.0
      org/splent_feature_auth@v1.0.0
      splent_feature_auth
    Output:
      auth
    """
    s = dep.strip()

    # org/repo@tag -> repo@tag
    if "/" in s:
        s = s.split("/", 1)[1]

    # drop @version
    s = s.split("@", 1)[0]

    # drop prefix
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
    """
    Returns (sorted_feature_names, root_name)
    """
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


def _write_csvconf_full(universe: list[str], selected: set[str]) -> str:
    """
    ConfigurationBasicReader expects CSV with 2 columns: feature,value
    """
    tmp = tempfile.NamedTemporaryFile("w", suffix=".csvconf", delete=False, encoding="utf-8", newline="")
    try:
        for feat in universe:
            tmp.write(f"{feat},{1 if feat in selected else 0}\n")
        tmp.flush()
        return tmp.name
    finally:
        tmp.close()


@click.command(
    "uvl:check",
    short_help="Validate pyproject feature selection against the downloaded UVL",
)
@click.option("--workspace", default="/workspace", show_default=True)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--print-config", is_flag=True, help="Print the generated 0/1 assignment")
def uvl_check(workspace, pyproject, print_config):
    # 1) Active product
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    # 2) Load pyproject
    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    # 3) UVL config + local file
    uvl_cfg = _get_uvl_cfg(data)
    mirror = uvl_cfg.get("mirror")
    doi = uvl_cfg.get("doi")
    file = uvl_cfg.get("file")

    if not mirror or not doi or not file:
        raise click.ClickException("Missing one of: mirror, doi, file in [tool.splent.uvl]")

    resolved_url = _resolve_uvlhub_raw_url(mirror, doi, file)

    local_uvl = os.path.join(product_path, "uvl", file)
    if not os.path.exists(local_uvl):
        raise click.ClickException(
            f"UVL not downloaded: {local_uvl}\n"
            f"Run: splent uvl:fetch\n"
            f"Expected URL: {resolved_url}"
        )

    # 4) Universe from UVL (robust)
    universe, root_name = _list_all_features_from_uvl(local_uvl)

    # 5) Selected from pyproject
    deps = _get_feature_deps(data)
    selected = {_normalize_feature_name(d) for d in deps}

    # root always selected even if not in pyproject
    selected.add(root_name)

    # 6) Sanity: unknown features in pyproject
    unknown = sorted([f for f in selected if f not in universe])
    if unknown:
        raise click.ClickException(f"pyproject contains features not present in UVL: {', '.join(unknown)}")

    # 7) Build full csvconf (0/1 for all universe features)
    conf_path = _write_csvconf_full(universe, selected)

    # 8) Validate using Flamapy facade
    try:
        fm = FLAMAFeatureModel(local_uvl)
        ok = fm.satisfiable_configuration(conf_path, full_configuration=False)
    finally:
        try:
            os.remove(conf_path)
        except OSError:
            pass

    # 9) Output
    click.echo()
    click.echo("UVL check")
    click.echo(f"Product      : {app_name}")
    click.echo(f"UVL file     : {local_uvl}")
    click.echo(f"UVL URL      : {resolved_url}")
    click.echo(f"Universe     : {len(universe)} features")
    click.echo(f"Selected     : {', '.join(sorted(selected))}")
    click.echo()

    if print_config:
        for feat in universe:
            click.echo(f"{feat}={1 if feat in selected else 0}")
        click.echo()

    if not ok:
        click.echo("FAIL: configuration is NOT satisfiable under the UVL constraints.", err=True)
        sys.exit(2)

    click.echo("OK: configuration is satisfiable.")
    click.echo()