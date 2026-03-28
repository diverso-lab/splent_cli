import click

from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel

from splent_cli.commands.spl.spl_utils import _resolve_spl
from splent_cli.commands.uvl.uvl_utils import (
    list_all_features_from_uvl as _list_all_features_from_uvl,
)


@click.command(
    "spl:configs",
    short_help="Print the number of valid configurations represented by the SPL's UVL model",
)
@click.argument("spl_name", required=False, default=None)
@click.option(
    "--with-sat",
    is_flag=True,
    help="Force PySAT backend (useful in some environments; slower sometimes)",
)
def spl_configs(spl_name, with_sat):
    """Show the number of valid configurations for the SPL.

    \b
    If SPL_NAME is given, uses it directly.
    Otherwise reads [tool.splent].spl from the active product.
    """
    name, uvl_path = _resolve_spl(spl_name)

    universe, _ = _list_all_features_from_uvl(uvl_path)

    fm = FLAMAFeatureModel(uvl_path)

    try:
        n = fm.configurations_number(with_sat=bool(with_sat))
    except TypeError:
        # Backward compatibility with versions where the param might not exist
        n = fm.configurations_number()

    click.echo()
    click.echo(f"SPL configs")
    click.echo(f"SPL      : {name}")
    click.echo(f"UVL      : {uvl_path}")
    click.echo(f"Features : {len(universe)}")
    click.echo()
    click.echo(f"Configurations : {n}")
    click.echo()


cli_command = spl_configs
