import os
import sys

import click

from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    resolve_uvl_path as _resolve_uvl_path,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    write_csvconf_full as _write_csvconf_full,
    print_uvl_header as _print_uvl_header,
)


@click.command(
    "uvl:valid",
    short_help="Validate an exact feature selection (args) against the product UVL",
)
@click.argument("features", nargs=-1)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--print-config", is_flag=True, help="Print generated 0/1 assignment")
def uvl_valid(features, pyproject, print_config):
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    local_uvl = _resolve_uvl_path(workspace, app_name, data)

    universe, root_name = _list_all_features_from_uvl(local_uvl)

    selected = set(features)
    selected.add(root_name)  # root always 1

    unknown = sorted([f for f in selected if f not in universe])
    if unknown:
        raise click.ClickException(
            f"Unknown feature(s) (not in UVL): {', '.join(unknown)}"
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

    _print_uvl_header("valid", app_name, local_uvl, len(universe))
    click.echo(f"Selected : {', '.join(sorted(selected))}")
    click.echo()

    if print_config:
        for feat in universe:
            click.echo(f"{feat}={1 if feat in selected else 0}")
        click.echo()

    if not ok:
        click.echo("UNSAT", err=True)
        sys.exit(2)

    click.echo("SAT")
    click.echo()
