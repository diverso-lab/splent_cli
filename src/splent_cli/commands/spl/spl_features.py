import click

from splent_cli.commands.spl.spl_utils import _resolve_spl
from splent_cli.commands.uvl.uvl_utils import (
    list_all_features_from_uvl as _list_all_features_from_uvl,
)
from splent_cli.services import context


@click.command(
    "spl:features",
    short_help="Print the list of features present in the SPL's UVL model",
)
@click.argument("spl_name")
@click.option("--no-root", is_flag=True, help="Do not print the root feature")
@context.requires_detached
def spl_features(spl_name, no_root):
    """List all features defined in the SPL's UVL model."""
    name, uvl_path = _resolve_spl(spl_name)

    feats, root_name = _list_all_features_from_uvl(uvl_path)

    click.echo()
    click.echo(f"SPL features")
    click.echo(f"SPL      : {name}")
    click.echo(f"UVL      : {uvl_path}")
    click.echo(f"Features : {len(feats)}")
    click.echo()

    for f in feats:
        if no_root and f == root_name:
            continue
        if f == root_name:
            click.echo(f"- {f}" + click.style("  (root)", fg="bright_black"))
        else:
            click.echo(f"- {f}")

    click.echo()


cli_command = spl_features
