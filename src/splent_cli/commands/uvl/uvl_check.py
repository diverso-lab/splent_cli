import os
import sys

import click

from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    get_uvl_cfg as _get_uvl_cfg,
    get_feature_deps as _get_feature_deps,
    normalize_feature_name as _normalize_feature_name,
    resolve_uvl_path as _resolve_uvl_path,
    resolve_uvlhub_raw_url as _resolve_uvlhub_raw_url,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    write_csvconf_full as _write_csvconf_full,
    print_uvl_header as _print_uvl_header,
)


def run_uvl_check(workspace: str) -> tuple[bool, str]:
    """
    Programmatic UVL validation. Returns (ok, message).
    Does not print anything and does not call sys.exit.
    """
    try:
        app_name = _read_splent_app(workspace=workspace)
        product_path = os.path.join(workspace, app_name)
        pyproject_path = os.path.join(product_path, "pyproject.toml")
        data = _load_pyproject(pyproject_path)
        try:
            local_uvl = _resolve_uvl_path(workspace, app_name, data)
        except Exception:
            return False, "UVL file not found. Check [tool.splent].spl or [tool.splent.uvl]."
        universe, root_name = _list_all_features_from_uvl(local_uvl)
        deps = _get_feature_deps(data)
        selected = {_normalize_feature_name(d) for d in deps}
        selected.add(root_name)
        unknown = sorted(f for f in selected if f not in universe)
        if unknown:
            return (
                False,
                f"pyproject contains features not in UVL: {', '.join(unknown)}",
            )
        conf_path = _write_csvconf_full(universe, selected)
        try:
            fm = FLAMAFeatureModel(local_uvl)
            ok = fm.satisfiable_configuration(conf_path, full_configuration=False)
        finally:
            try:
                os.remove(conf_path)
            except OSError:
                pass
        if not ok:
            return False, "Configuration is NOT satisfiable under the UVL constraints."
        return True, "OK"
    except Exception as exc:
        return False, str(exc)


@click.command(
    "uvl:check",
    short_help="Validate pyproject feature selection against the downloaded UVL",
)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--print-config", is_flag=True, help="Print the generated 0/1 assignment")
def uvl_check(pyproject, print_config):
    workspace = str(context.workspace())
    # 1) Active product
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    # 2) Load pyproject
    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    # 3) Resolve UVL (catalog or legacy)
    local_uvl = _resolve_uvl_path(workspace, app_name, data)

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
        raise click.ClickException(
            f"pyproject contains features not present in UVL: {', '.join(unknown)}"
        )

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
    _print_uvl_header("check", app_name, local_uvl, len(universe))
    click.echo(f"Selected : {', '.join(sorted(selected))}")
    click.echo()

    if print_config:
        for feat in universe:
            click.echo(f"{feat}={1 if feat in selected else 0}")
        click.echo()

    if not ok:
        click.echo(
            "FAIL: configuration is NOT satisfiable under the UVL constraints.",
            err=True,
        )
        sys.exit(2)

    click.echo("OK: configuration is satisfiable.")
    click.echo()
