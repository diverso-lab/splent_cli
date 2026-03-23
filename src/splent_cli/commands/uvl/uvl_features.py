import os

import click

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    get_uvl_cfg as _get_uvl_cfg,
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

    uvl_cfg = _get_uvl_cfg(data)
    file = uvl_cfg.get("file")
    if not file:
        raise click.ClickException("Missing [tool.splent.uvl].file in pyproject.toml")

    local_uvl = os.path.join(product_path, "uvl", file)
    if not os.path.exists(local_uvl):
        raise click.ClickException(
            f"UVL not downloaded: {local_uvl} (run: splent uvl:fetch)"
        )

    feats, root_name = _list_all_features_from_uvl(local_uvl)

    _print_uvl_header("features", app_name, local_uvl, len(feats))

    for f in feats:
        if no_root and f == root_name:
            continue
        click.echo(f"- {f}")

    click.echo()
