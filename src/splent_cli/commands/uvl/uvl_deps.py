import os
import re
from collections import defaultdict, deque
from pathlib import Path

import click
import tomllib


def _read_splent_app(workspace: str = "/workspace") -> str:
    env_path = os.path.join(workspace, ".env")
    if not os.path.exists(env_path):
        raise click.ClickException("Missing /workspace/.env (run: splent product:select <app>)")

    app_name = None
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SPLENT_APP="):
                app_name = line.strip().split("=", 1)[1]

    if not app_name:
        raise click.ClickException("SPLENT_APP not set in /workspace/.env (run: splent product:select <app>)")

    product_path = os.path.join(workspace, app_name)
    if not os.path.isdir(product_path):
        raise click.ClickException(f"Active product not found: {product_path}")

    return app_name


def _load_pyproject(pyproject_path: str) -> dict:
    p = Path(pyproject_path)
    if not p.exists():
        raise click.ClickException(f"Missing {pyproject_path}")
    with open(p, "rb") as f:
        return tomllib.load(f)


def _get_uvl_cfg(data: dict) -> dict:
    try:
        return data["tool"]["splent"]["uvl"]
    except KeyError:
        raise click.ClickException("Missing [tool.splent.uvl] in pyproject.toml")


def _extract_implications_from_uvl_text(uvl_text: str) -> list[tuple[str, str]]:
    pairs = []
    for line in uvl_text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        line = line.replace("=&gt;", "=>")
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=>\s*([A-Za-z_][A-Za-z0-9_]*)\s*$", line)
        if m:
            pairs.append((m.group(1), m.group(2)))
    return pairs


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
