import os
from collections import defaultdict, deque
from pathlib import Path

import click

from splent_cli.commands.uvl.uvl_utils import (
    read_splent_app as _read_splent_app,
    load_pyproject as _load_pyproject,
    get_uvl_cfg as _get_uvl_cfg,
    extract_implications_from_uvl_text as _extract_implications_from_uvl_text,
)


def _build_graph(pairs: list[tuple[str, str]]):
    req = defaultdict(set)   # A -> {B}
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
@click.option("--reverse", is_flag=True, help="Show reverse dependencies (who requires this feature)")
@click.option("--workspace", default="/workspace", show_default=True)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
def uvl_deps(feature, reverse, workspace, pyproject):
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
        raise click.ClickException(f"UVL not downloaded: {local_uvl} (run: splent uvl:fetch)")

    uvl_text = Path(local_uvl).read_text(encoding="utf-8", errors="replace")
    pairs = _extract_implications_from_uvl_text(uvl_text)
    req, rreq = _build_graph(pairs)

    graph = rreq if reverse else req
    deps = _closure(feature, graph)

    click.echo()
    click.echo("UVL deps")
    click.echo(f"Product : {app_name}")
    click.echo(f"UVL     : {local_uvl}")
    click.echo()

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
