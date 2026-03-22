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
    resolve_uvlhub_raw_url as _resolve_uvlhub_raw_url,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    write_csvconf_full as _write_csvconf_full,
    print_uvl_header as _print_uvl_header,
)


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

    # 3) UVL config + local file
    uvl_cfg = _get_uvl_cfg(data)
    mirror = uvl_cfg.get("mirror")
    doi = uvl_cfg.get("doi")
    file = uvl_cfg.get("file")

    if not mirror or not doi or not file:
        raise click.ClickException(
            "Missing one of: mirror, doi, file in [tool.splent.uvl]"
        )

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
    click.echo(f"URL      : {resolved_url}")
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
