import os
import sys

import click

from flamapy.interfaces.python.flamapy_feature_model import FLAMAFeatureModel

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    get_feature_deps as _get_feature_deps,
    normalize_feature_name as _normalize_feature_name,
    resolve_uvl_path as _resolve_uvl_path,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    write_csvconf_full as _write_csvconf_full,
    print_uvl_header as _print_uvl_header,
    run_uvl_check as _run_uvl_check,
)


@click.command(
    "product:validate",
    short_help="Validate the product configuration against the SPL.",
)
@click.option("--features", "feature_list", default=None, help="Comma-separated feature list to validate (instead of pyproject).")
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--print-config", is_flag=True, help="Print the generated 0/1 assignment")
def product_validate(feature_list, pyproject, print_config):
    context.require_app()
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    local_uvl = _resolve_uvl_path(workspace, app_name, data)

    universe, root_name = _list_all_features_from_uvl(local_uvl)

    if feature_list:
        # Explicit feature list mode (was uvl:valid)
        selected = set(f.strip() for f in feature_list.split(",") if f.strip())
        selected.add(root_name)

        unknown = sorted([f for f in selected if f not in universe])
        if unknown:
            raise click.ClickException(
                f"Unknown feature(s) (not in UVL): {', '.join(unknown)}"
            )
    else:
        # Pyproject mode — include env-specific features (dev/prod)
        from splent_cli.utils.feature_utils import read_features_from_data
        env = os.getenv("SPLENT_ENV", "dev")
        deps = read_features_from_data(data, env)
        selected = {_normalize_feature_name(d) for d in deps}
        selected.add(root_name)

        unknown = sorted([f for f in selected if f not in universe])
        if unknown:
            raise click.ClickException(
                f"pyproject contains features not present in UVL: {', '.join(unknown)}"
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

    _print_uvl_header("validate", app_name, local_uvl, len(universe))
    click.echo(f"Selected : {', '.join(sorted(selected))}")
    click.echo()

    if print_config:
        for feat in universe:
            click.echo(f"{feat}={1 if feat in selected else 0}")
        click.echo()

    if not ok:
        if feature_list:
            click.echo("UNSAT", err=True)
        else:
            click.echo(
                "FAIL: configuration is NOT satisfiable under the UVL constraints.",
                err=True,
            )
        sys.exit(2)

    if feature_list:
        click.echo("SAT")
    else:
        click.echo("OK: configuration is satisfiable.")
    click.echo()


cli_command = product_validate
