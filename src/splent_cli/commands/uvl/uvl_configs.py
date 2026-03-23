import os

import click

from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    get_uvl_cfg as _get_uvl_cfg,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    print_uvl_header as _print_uvl_header,
)


@click.command(
    "uvl:configs",
    short_help="Print the number of valid configurations represented by the UVL model",
)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option(
    "--with-sat",
    is_flag=True,
    help="Force PySAT backend (useful in some environments; slower sometimes)",
)
def uvl_configs(pyproject, with_sat):
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

    universe, _ = _list_all_features_from_uvl(local_uvl)

    fm = FLAMAFeatureModel(local_uvl)

    try:
        n = fm.configurations_number(with_sat=bool(with_sat))
    except TypeError:
        # Backward compatibility with versions where the param might not exist
        n = fm.configurations_number()

    _print_uvl_header("configs", app_name, local_uvl, len(universe))
    click.echo(f"Configurations : {n}")
    click.echo()
