"""
spl:remove-feature — Take a feature out of an SPL, with its constraints.

The counterpart of spl:add-feature. A model outlives the reasons it was
written: a feature is planned and declared before it is built, or it is
replaced, and what stays behind is a model offering a product nobody can
derive. Editing the .uvl by hand to fix that is how a model and its
constraints drift apart, because the feature is easy to see and the
constraints mentioning it are three screens further down.

Refuses while another feature still depends on it, since removing the
feature but keeping "a => removed" leaves a model that no longer parses
into a satisfiable configuration.
"""

import re

import click

from splent_cli.services import context
from splent_cli.commands.spl.spl_utils import _resolve_spl
from splent_cli.commands.spl.spl_add_feature import _parse_uvl_packages
from splent_cli.utils.io_utils import atomic_write, backup_file


CONSTRAINT = re.compile(r"^\s*(\w+)\s*=>\s*(\w+)\s*$")


def _constraint_lines(lines: list[str]) -> list[tuple[int, str, str]]:
    """Return [(index, left, right)] for every "a => b" line in the model."""
    found = []
    for i, line in enumerate(lines):
        m = CONSTRAINT.match(line)
        if m:
            found.append((i, m.group(1), m.group(2)))
    return found


@click.command(
    "spl:remove-feature",
    short_help="Remove a feature from an SPL, with the constraints naming it.",
)
@click.argument("spl_name")
@click.argument("feature")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Remove even when other features depend on it, dropping their constraints.",
)
@context.requires_detached
def spl_remove_feature(spl_name, feature, force):
    """Remove a feature from an SPL variability model.

    FEATURE is the short name ("oidc") or the package
    ("splent_feature_oidc").

    \b
    Example:
      splent spl:remove-feature openwiki_spl oidc
    """
    _, uvl_path = _resolve_spl(spl_name)

    package_map = _parse_uvl_packages(uvl_path)

    short = feature
    if short.startswith("splent_feature_"):
        short = short[len("splent_feature_") :]
    if short not in package_map:
        by_package = {v: k for k, v in package_map.items()}
        if feature in by_package:
            short = by_package[feature]
        else:
            click.secho(f"  {feature} is not declared in {spl_name}.", fg="yellow")
            click.echo(f"  Known: {', '.join(sorted(package_map))}")
            raise SystemExit(1)

    with open(uvl_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    # Who would be left pointing at nothing.
    dependents = sorted(
        {left for _, left, right in _constraint_lines(lines) if right == short}
    )
    if dependents and not force:
        click.secho(
            f"  {short} cannot be removed: {', '.join(dependents)} "
            f"{'depends' if len(dependents) == 1 else 'depend'} on it.",
            fg="red",
        )
        click.echo(
            click.style(
                "  Remove those first, or pass --force to drop their "
                "constraints along with it.",
                fg="bright_black",
            )
        )
        raise SystemExit(1)

    # The declaration, and every constraint that names it on either side. Both
    # go, because a constraint mentioning a feature the model no longer has is
    # not a constraint any solver can read.
    declaration = re.compile(rf"^\s*{re.escape(short)}\s*\{{")
    dropped_constraints = []
    kept = []
    for line in lines:
        m = CONSTRAINT.match(line)
        if m and short in (m.group(1), m.group(2)):
            dropped_constraints.append(line.strip())
            continue
        if declaration.match(line):
            continue
        kept.append(line)

    # UVL rejects an empty "constraints" section, so it goes too when the last
    # constraint in the model was one of the ones just dropped.
    if not any(CONSTRAINT.match(line) for line in kept):
        kept = [line for line in kept if line.strip() != "constraints"]

    # Section headers left with nothing under them ("optional" holding only
    # this feature) would also fail to parse.
    kept = _drop_empty_groups(kept)

    bak = backup_file(uvl_path, ".bak")
    atomic_write(uvl_path, "\n".join(kept) + "\n")

    # Re-read and confirm, restoring the original rather than leaving a model
    # that no longer parses. Same contract as spl:add-feature.
    try:
        remaining = _parse_uvl_packages(uvl_path)
    except Exception as exc:  # noqa: BLE001
        remaining, parse_error = {}, exc
    else:
        parse_error = None

    if parse_error is not None or short in remaining:
        if bak is not None:
            atomic_write(uvl_path, bak.read_text(encoding="utf-8"))
        click.secho(
            f"  UVL did not validate after editing"
            f"{f' ({parse_error})' if parse_error else ''}. The original was "
            f"restored" + (f" from {bak}." if bak is not None else "."),
            fg="red",
        )
        raise SystemExit(1)

    click.echo()
    click.secho(f"  Removed '{short}' from {spl_name}.", fg="green")
    for constraint in dropped_constraints:
        click.echo(click.style(f"     dropped constraint: {constraint}", dim=True))
    click.echo()


def _drop_empty_groups(lines: list[str]) -> list[str]:
    """Remove group headers that no longer have anything under them.

    A header keeps its meaning only through indentation, so a group is empty
    when the next non-blank line is indented no deeper than the header itself.
    """
    group_headers = {"optional", "mandatory", "alternative", "or"}

    def indent(text: str) -> int:
        return len(text) - len(text.lstrip())

    result = list(lines)
    changed = True
    while changed:
        changed = False
        for i, line in enumerate(result):
            if line.strip() not in group_headers:
                continue
            following = next(
                (nxt for nxt in result[i + 1 :] if nxt.strip()),
                None,
            )
            if following is None or indent(following) <= indent(line):
                del result[i]
                changed = True
                break
    return result


cli_command = spl_remove_feature
