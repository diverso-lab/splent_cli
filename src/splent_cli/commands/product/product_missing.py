import os
from pathlib import Path

import click

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    resolve_uvl_path as _resolve_uvl_path,
    get_feature_deps as _get_feature_deps,
    normalize_feature_name as _normalize_feature_name,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    extract_implications_from_uvl_text as _extract_implications_from_uvl_text,
    print_uvl_header as _print_uvl_header,
)


@click.command(
    "product:missing",
    short_help="List missing required features according to UVL constraints (based on pyproject selection).",
)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option(
    "--fail", is_flag=True, help="Exit with code 2 if missing dependencies are found"
)
def product_missing(pyproject, fail):
    context.require_app()
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    local_uvl = _resolve_uvl_path(workspace, app_name, data)

    # universe + root
    universe, root_name = _list_all_features_from_uvl(local_uvl)

    # selection from pyproject
    deps = _get_feature_deps(data)
    selected = {_normalize_feature_name(d) for d in deps}
    selected.add(root_name)  # always selected

    # sanity: selected must exist
    unknown = sorted([f for f in selected if f not in universe])
    if unknown:
        raise click.ClickException(
            f"pyproject contains features not present in UVL: {', '.join(unknown)}"
        )

    # parse implications from UVL text
    uvl_text = Path(local_uvl).read_text(encoding="utf-8", errors="replace")
    implications = _extract_implications_from_uvl_text(uvl_text)

    # compute missing
    violations = []
    missing_features = set()

    for a, b in implications:
        if a in selected and b not in selected:
            violations.append((a, b))
            missing_features.add(b)

    _print_uvl_header("missing", app_name, local_uvl, len(universe))
    click.echo(f"Selected : {', '.join(sorted(selected))}")
    click.echo()

    if not violations:
        click.echo("OK: no missing dependencies (implication constraints satisfied).")
        click.echo()
        return

    click.echo("Missing dependencies detected:")
    for a, b in violations:
        click.echo(f"- {a} requires {b}")

    click.echo()
    click.echo(f"Missing features to add: {', '.join(sorted(missing_features))}")
    click.echo()

    if fail:
        raise click.ClickException("Missing dependencies found")


cli_command = product_missing
