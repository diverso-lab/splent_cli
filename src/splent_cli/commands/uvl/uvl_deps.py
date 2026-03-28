import os
from collections import defaultdict, deque
from pathlib import Path

import click

from splent_cli.services import context
from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    resolve_uvl_path as _resolve_uvl_path,
    list_all_features_from_uvl as _list_all_features_from_uvl,
    extract_implications_from_uvl_text as _extract_implications_from_uvl_text,
    print_uvl_header as _print_uvl_header,
)


def _build_graph(pairs: list[tuple[str, str]]):
    req = defaultdict(set)  # A -> {B}
    rreq = defaultdict(set)  # B -> {A}
    for a, b in pairs:
        req[a].add(b)
        rreq[b].add(a)
    return req, rreq


def _closure(start: str, graph: dict[str, set[str]]) -> list[str]:
    seen = set()
    q = deque([start])
    while q:
        x = q.popleft()
        for y in graph.get(x, set()):
            if y not in seen:
                seen.add(y)
                q.append(y)
    return sorted(seen)


@click.command(
    "uvl:deps",
    short_help="Show dependencies implied by UVL constraints (A => B) for a feature",
)
@click.argument("feature", required=True)
@click.option(
    "--reverse",
    is_flag=True,
    help="Show reverse dependencies (who requires this feature)",
)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
def uvl_deps(feature, reverse, pyproject):
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    local_uvl = _resolve_uvl_path(workspace, app_name, data)

    universe, _ = _list_all_features_from_uvl(local_uvl)

    uvl_text = Path(local_uvl).read_text(encoding="utf-8", errors="replace")
    pairs = _extract_implications_from_uvl_text(uvl_text)
    req, rreq = _build_graph(pairs)

    graph = rreq if reverse else req
    deps = _closure(feature, graph)

    _print_uvl_header("deps", app_name, local_uvl, len(universe))

    if reverse:
        click.echo(f"Reverse dependencies of '{feature}' (features that require it):")
    else:
        click.echo(f"Dependencies of '{feature}' (features it requires):")

    if not deps:
        click.echo("- (none)")
        click.echo()
        return

    for d in deps:
        click.echo(f"- {d}")

    click.echo()
