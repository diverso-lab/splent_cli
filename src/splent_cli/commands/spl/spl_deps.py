from collections import defaultdict, deque
from pathlib import Path

import click

from splent_cli.commands.spl.spl_utils import _resolve_spl
from splent_cli.commands.uvl.uvl_utils import (
    list_all_features_from_uvl as _list_all_features_from_uvl,
    extract_implications_from_uvl_text as _extract_implications_from_uvl_text,
)
from splent_cli.services import context


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
    "spl:deps",
    short_help="Show dependencies implied by UVL constraints (A => B) for a feature",
)
@click.argument("feature")
@click.argument("spl_name")
@click.option(
    "--reverse",
    is_flag=True,
    help="Show reverse dependencies (who requires this feature)",
)
@context.requires_detached
def spl_deps(feature, spl_name, reverse):
    """Show dependency closure for FEATURE in the SPL's UVL model."""
    name, uvl_path = _resolve_spl(spl_name)

    universe, _ = _list_all_features_from_uvl(uvl_path)

    uvl_text = Path(uvl_path).read_text(encoding="utf-8", errors="replace")
    pairs = _extract_implications_from_uvl_text(uvl_text)
    req, rreq = _build_graph(pairs)

    graph = rreq if reverse else req
    deps = _closure(feature, graph)

    click.echo()
    click.echo(f"SPL deps")
    click.echo(f"SPL      : {name}")
    click.echo(f"UVL      : {uvl_path}")
    click.echo(f"Features : {len(universe)}")
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


cli_command = spl_deps
