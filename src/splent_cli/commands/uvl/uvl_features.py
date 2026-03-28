import os

import click

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    resolve_uvl_path as _resolve_uvl_path,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    print_uvl_header as _print_uvl_header,
)


@click.command(
    "uvl:features",
    short_help="Print the list of features present in the downloaded UVL model",
)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--no-root", is_flag=True, help="Do not print the root feature")
def uvl_features(pyproject, no_root):
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    local_uvl = _resolve_uvl_path(workspace, app_name, data)

    feats, root_name = _list_all_features_from_uvl(local_uvl)

    _print_uvl_header("features", app_name, local_uvl, len(feats))

    for f in feats:
        if no_root and f == root_name:
            continue
        click.echo(f"- {f}")

    click.echo()
