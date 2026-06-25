import os
import re
from collections import defaultdict, deque
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


# -------------------------
# UVL parsing: constraints + per-feature metadata
# -------------------------


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
        auth {org 'splent-io', package 'splent_feature_auth'}
    Returns:
      meta['auth'] = {'org': 'splent-io', 'package': 'splent_feature_auth'}
    Notes:
      - Works on your current UVL formatting.
      - Ignores features without {...}.
    """
    meta: dict[str, dict] = {}

    # captures: <indent><name> { ... }
    for m in re.finditer(
        r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\{([^}]*)\}\s*$", uvl_text, flags=re.MULTILINE
    ):
        fname = m.group(1)
        body = m.group(2)

        fields = {}
        # capture key 'value' or key "value" (single or double quotes)
        for kv in re.finditer(
            r"""([A-Za-z_][A-Za-z0-9_]*)\s*(?:'([^']*)'|"([^"]*)")\s*""", body
        ):
            fields[kv.group(1)] = (
                kv.group(2) if kv.group(2) is not None else kv.group(3)
            )

        if fields:
            meta[fname] = fields

    return meta


def _dep_spec_from_meta(
    feature_name: str, meta: dict[str, dict], default_org: str | None = None
) -> str:
    """
    Build dependency string to put in pyproject optional-dependencies.features.
    Versions are managed via feature:attach, not stored in the UVL.
    Returns: org/package  (or just package if no org).
    """
    fields = meta.get(feature_name)
    if not fields:
        raise click.ClickException(
            f"UVL metadata missing for feature '{feature_name}'"
            " (no {org, package} found)"
        )

    org = fields.get("org")
    pkg = fields.get("package")

    if not pkg:
        raise click.ClickException(
            f"UVL metadata missing 'package' for feature '{feature_name}'"
        )

    if org:
        return f"{org}/{pkg}"

    return pkg


# -------------------------
# Patch pyproject.toml (surgical update)
# -------------------------


@click.command(
    "product:auto-require",
    short_help="Auto-add missing required features to pyproject.toml from UVL constraints.",
)
@click.option("--pyproject", default=None, help="Override pyproject.toml path")
@click.option(
    "--default-org",
    default=None,
    help="If set, omit org/ prefix for that org when writing deps",
)
@click.option("--yes", is_flag=True, help="Apply changes without prompting")
@click.option(
    "--dry-run", is_flag=True, help="Only show what would change; do not modify files"
)
def product_complete(pyproject, default_org, yes, dry_run):
    context.require_app()
    workspace = str(context.workspace())
    app_name = _read_splent_app(workspace=workspace)
    product_path = os.path.join(workspace, app_name)

    pyproject_path = pyproject or os.path.join(product_path, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    local_uvl = _resolve_uvl_path(workspace, app_name, data)

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
        raise click.ClickException(
            f"UVL constraint refers to unknown features: {', '.join(unknown_missing)}"
        )

    if not missing_names:
        _print_uvl_header("complete", app_name, local_uvl, len(universe))
        click.echo("OK: nothing to add (no missing dependencies).")
        click.echo()
        return

    # map missing feature names -> dep specs using UVL metadata
    meta = _parse_feature_metadata_from_uvl_text(uvl_text)

    to_add_specs = []
    for fname in missing_names:
        spec = _dep_spec_from_meta(fname, meta, default_org=default_org)
        to_add_specs.append(spec)

    _print_uvl_header("complete", app_name, local_uvl, len(universe))
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

    # Apply patch: parse pyproject as TOML, append missing specs to the base
    # features list (dedup, preserving order), then write back atomically with
    # a backup. Avoids fragile in-place regex edits of hand-formatted blocks.
    import tomli_w
    from splent_cli.utils.feature_utils import write_features_to_data
    from splent_cli.utils.io_utils import (
        atomic_write,
        backup_file,
        load_toml,
    )

    current_data = load_toml(pyproject_path, what="pyproject.toml")
    current_features = _get_feature_deps(current_data)

    existing = set(current_features)
    new_features = list(current_features)
    for spec in to_add_specs:
        if spec not in existing:
            new_features.append(spec)
            existing.add(spec)

    # If nothing changes (already present), say so
    if new_features == current_features:
        click.echo("No changes needed (entries already present).")
        click.echo()
        return

    write_features_to_data(current_data, new_features)
    backup_file(pyproject_path, ".bak")
    atomic_write(pyproject_path, tomli_w.dumps(current_data))
    click.echo(f"Updated: {pyproject_path}")
    click.echo()


cli_command = product_complete
