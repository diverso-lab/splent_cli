import os
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import List

import click
import tomllib

from flamapy.core.discover import DiscoverMetamodels


# -------------------------
# Common helpers (same style as yours)
# -------------------------

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


def _get_feature_deps(data: dict) -> list[str]:
    return (
        data.get("project", {})
        .get("optional-dependencies", {})
        .get("features", [])
    )


def _normalize_feature_name(dep: str) -> str:
    """
    Accept:
      splent_feature_auth@v1.0.0
      org/splent_feature_auth@v1.0.0
    Output:
      auth
    """
    s = dep.strip()
    if "/" in s:
        s = s.split("/", 1)[1]
    s = s.split("@", 1)[0]
    if s.startswith("splent_feature_"):
        s = s[len("splent_feature_"):]
    if not s or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", s):
        raise click.ClickException(f"Cannot normalize feature dependency: {dep}")
    return s


def _iter_children(node):
    if hasattr(node, "children") and node.children is not None:
        return list(node.children)
    if hasattr(node, "get_children"):
        return list(node.get_children())
    return []


def _get_root_feature(fm):
    for attr in ("root", "root_feature"):
        if hasattr(fm, attr):
            r = getattr(fm, attr)
            return r() if callable(r) else r
    if hasattr(fm, "get_root"):
        return fm.get_root()
    raise click.ClickException("Cannot access root feature from Flamapy FM model object")


def _list_all_features_from_uvl(uvl_path: str) -> tuple[list[str], str]:
    dm = DiscoverMetamodels()
    fm = dm.use_transformation_t2m(uvl_path, "fm")

    root = _get_root_feature(fm)
    root_name = getattr(root, "name", None)
    if not isinstance(root_name, str) or not root_name:
        raise click.ClickException("Cannot determine root feature name from UVL")

    seen = set()
    names = []
    stack = [root]

    while stack:
        n = stack.pop()
        name = getattr(n, "name", None)
        if isinstance(name, str) and name and name not in seen:
            seen.add(name)
            names.append(name)
        stack.extend(_iter_children(n))

    return sorted(names), root_name


# -------------------------
# UVL parsing: constraints + per-feature metadata
# -------------------------

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


def _build_req_graph(pairs: list[tuple[str, str]]) -> dict[str, set[str]]:
    req = defaultdict(set)
    for a, b in pairs:
        req[a].add(b)
    return req


def _closure_requires(selected: set[str], req_graph: dict[str, set[str]]) -> set[str]:
    """
    Return all features required (direct + transitive) by 'selected' via A=>B edges.
    """
    out = set()
    q = deque(selected)
    while q:
        a = q.popleft()
        for b in req_graph.get(a, set()):
            if b not in out:
                out.add(b)
                q.append(b)
    return out


def _parse_feature_metadata_from_uvl_text(uvl_text: str) -> dict[str, dict]:
    """
    Parse lines like:
        auth {org 'splent-io', package 'splent_feature_auth', version 'v1_0_0'}
    Returns:
      meta['auth'] = {'org': 'splent-io', 'package': 'splent_feature_auth', 'version': 'v1_0_0'}
    Notes:
      - Works on your current UVL formatting.
      - Ignores features without {...}.
    """
    meta: dict[str, dict] = {}

    # captures: <indent><name> { ... }
    for m in re.finditer(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}\s*$", uvl_text, flags=re.MULTILINE):
        fname = m.group(1)
        body = m.group(2)

        fields = {}
        # capture key 'value' (single quotes), allow commas/spaces
        for kv in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*'([^']*)'\s*", body):
            fields[kv.group(1)] = kv.group(2)

        if fields:
            meta[fname] = fields

    return meta


def _normalize_uvl_version(v: str) -> str:
    """
    Your UVL stores version like v1_0_0, but you want v1.0.0 for pyproject spec.
    If already has dots, keep.
    """
    if v is None:
        return v
    if re.match(r"^v\d+_\d+_\d+$", v):
        return v.replace("_", ".")
    return v


def _dep_spec_from_meta(feature_name: str, meta: dict[str, dict], default_org: str | None = None) -> str:
    """
    Build dependency string to put in pyproject optional-dependencies.features
    Prefer: org/package@version
    If org missing, fallback to package@version.
    """
    fields = meta.get(feature_name)
    if not fields:
        raise click.ClickException(f"UVL metadata missing for feature '{feature_name}' (no {{org, package, version}} found)")

    org = fields.get("org")
    pkg = fields.get("package")
    ver = _normalize_uvl_version(fields.get("version"))

    if not pkg:
        raise click.ClickException(f"UVL metadata missing 'package' for feature '{feature_name}'")
    if not ver:
        raise click.ClickException(f"UVL metadata missing 'version' for feature '{feature_name}'")

    # if you want to omit org when it's the default, you can do it here
    if org and default_org and org == default_org:
        return f"{pkg}@{ver}"

    if org:
        return f"{org}/{pkg}@{ver}"

    return f"{pkg}@{ver}"


# -------------------------
# Patch pyproject.toml (surgical update)
# -------------------------


def _extract_string_items(list_body: str) -> List[str]:
    """
    Extrae strings TOML dentro de un bloque de lista, soportando "..." y '...'.
    Ignora comentarios y whitespace. No intenta soportar escapes complejos.
    """
    items = []

    # quita comentarios // o # al final de línea (muy común en tus pyproject)
    cleaned_lines = []
    for line in list_body.splitlines():
        line2 = re.sub(r"(\s+#.*)$", "", line)
        line2 = re.sub(r"(\s+//.*)$", "", line2)
        cleaned_lines.append(line2)

    cleaned = "\n".join(cleaned_lines)

    # captura "..." o '...'
    for m in re.finditer(r'["\']([^"\']+)["\']', cleaned):
        items.append(m.group(1))

    return items


def _render_features_block(items: List[str], indent_key: str, indent_item: str) -> str:
    """
    Renderiza:
      features = [
          "...",
          "...",
      ]
    """
    out = []
    out.append(f"{indent_key}features = [\n")
    for it in items:
        out.append(f'{indent_item}"{it}",\n')
    out.append(f"{indent_key}]\n")
    return "".join(out)


def _rewrite_pyproject_features_block(py_text: str, to_add: List[str]) -> str:
    """
    Reescribe el bloque completo 'features = [ ... ]' manteniendo el resto intacto.
    Busca el bloque dentro del archivo, no depende de tabulaciones exactas.
    """
    if not to_add:
        return py_text

    # Captura:
    # 1) indent del key (espacios antes de "features")
    # 2) cuerpo de la lista
    #
    # Importante: asumimos que el ']' está en su propia línea (como en tu pyproject generado).
    pattern = re.compile(
        r"(?ms)^(?P<indent>\s*)features\s*=\s*\[\s*\n(?P<body>.*?)(?P=indent)\]\s*$"
    )

    m = pattern.search(py_text)
    if not m:
        raise ValueError("Cannot find 'features = [ ... ]' block in pyproject.toml")

    indent_key = m.group("indent")
    body = m.group("body")

    current = _extract_string_items(body)

    # añade sin duplicar, preservando orden original
    existing = set(current)
    new_items = list(current)
    for x in to_add:
        if x not in existing:
            new_items.append(x)
            existing.add(x)

    # decide indent de items: 4 espacios más que el key (tu estilo actual)
    indent_item = indent_key + "    "

    new_block = _render_features_block(new_items, indent_key=indent_key, indent_item=indent_item)

    # reemplaza exactamente el match completo por el bloque regenerado
    return py_text[: m.start()] + new_block + py_text[m.end() :]

@click.command(
    "uvl:sync",
    short_help="Add missing required features to pyproject optional-dependencies.features using UVL metadata",
)
@click.option("--workspace", default="/workspace", show_default=True)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option("--default-org", default=None, help="If set, omit org/ prefix for that org when writing deps")
@click.option("--yes", is_flag=True, help="Apply changes without prompting")
@click.option("--dry-run", is_flag=True, help="Only show what would change; do not modify files")
def uvl_sync(workspace, pyproject, default_org, yes, dry_run):
    ...
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

    # universe + root
    universe, root_name = _list_all_features_from_uvl(local_uvl)
    universe_set = set(universe)

    # current selection from pyproject
    deps = _get_feature_deps(data)
    selected_names = {_normalize_feature_name(d) for d in deps}
    selected_names.add(root_name)  # root always selected

    # compute missing via implications
    pairs = _extract_implications_from_uvl_text(uvl_text)
    req_graph = _build_req_graph(pairs)
    required = _closure_requires(selected_names, req_graph)

    missing_names = sorted([f for f in required if f not in selected_names])

    # filter out root just in case
    missing_names = [f for f in missing_names if f != root_name]

    # sanity
    unknown_missing = sorted([f for f in missing_names if f not in universe_set])
    if unknown_missing:
        raise click.ClickException(f"UVL constraint refers to unknown features: {', '.join(unknown_missing)}")

    if not missing_names:
        click.echo()
        click.echo("UVL sync")
        click.echo(f"Product : {app_name}")
        click.echo("OK: nothing to add (no missing dependencies).")
        click.echo()
        return

    # map missing feature names -> dep specs using UVL metadata
    meta = _parse_feature_metadata_from_uvl_text(uvl_text)

    to_add_specs = []
    for fname in missing_names:
        spec = _dep_spec_from_meta(fname, meta, default_org=default_org)
        to_add_specs.append(spec)

    click.echo()
    click.echo("UVL sync")
    click.echo(f"Product : {app_name}")
    click.echo(f"UVL     : {local_uvl}")
    click.echo()
    click.echo("Missing features (by name):")
    for f in missing_names:
        click.echo(f"- {f}")
    click.echo()
    click.echo("Will add to pyproject [project.optional-dependencies].features:")
    for s in to_add_specs:
        click.echo(f"- {s}")
    click.echo()

    if dry_run:
        click.echo("Dry-run (use --yes or confirm prompt to modify pyproject.toml).")
        click.echo()
        return

    do_apply = yes
    if not yes:
        do_apply = click.confirm("Add these entries to pyproject.toml?", default=False)

    if not do_apply:
        click.echo("Aborted. No changes made.")
        click.echo()
        return

    # Apply patch
    py_text = Path(pyproject_path).read_text(encoding="utf-8", errors="replace")
    new_text = _rewrite_pyproject_features_block(py_text, to_add_specs)

    # If nothing changes (already present), say so
    if new_text == py_text:
        click.echo("No changes needed (entries already present).")
        click.echo()
        return

    Path(pyproject_path).write_text(new_text, encoding="utf-8")
    click.echo(f"Updated: {pyproject_path}")
    click.echo()