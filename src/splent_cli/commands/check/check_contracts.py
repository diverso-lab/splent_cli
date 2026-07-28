"""
check:contracts — Verify that feature contracts and SPL UVL models agree.

The dependency truth must not be split: a feature's requires.features (in
[tool.splent.contract]) and the SPL's UVL implications (A => B) describe
the same reality from two sides. This check reports:

  - contract dependencies with no matching UVL constraint (ERROR: the SPL
    would let a product select A without B and break at runtime), and
  - UVL implications not backed by any contract (WARNING: the dependency
    is invisible to feature:install and the marketplace).
"""

from pathlib import Path

import click
import tomllib

from splent_cli.services import context, marketplace, spl_store


def _snapshot_version_key(pyproject_path: Path) -> tuple:
    """Semver sort key for a cache snapshot dir like splent_feature_x@v1.10.0."""
    import re

    _, _, version = pyproject_path.parent.name.partition("@")
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return (-1, -1, -1)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _local_contract_requires(workspace: str, package: str) -> list[str] | None:
    """requires.features of a feature found locally (workspace root, else the
    newest cache snapshot by semver). None when not available locally."""
    candidates = [Path(workspace) / package / "pyproject.toml"]
    cache = Path(workspace) / ".splent_cache" / "features"
    if cache.is_dir():
        candidates.extend(
            sorted(
                cache.glob(f"*/{package}@*/pyproject.toml"),
                key=_snapshot_version_key,
                reverse=True,
            )
        )

    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        requires = (
            data.get("tool", {})
            .get("splent", {})
            .get("contract", {})
            .get("requires", {})
        )
        return {
            "features": requires.get("features", []),
            "optional": requires.get("features_optional", []),
        }
    return None


@click.command(
    "check:contracts",
    short_help="Check that feature contracts and SPL UVL constraints agree.",
)
@click.option("--spl", "spl_name", default=None, help="Check a single SPL.")
def check_contracts(spl_name):
    """
    Cross-check requires.features in every contract against the UVL
    constraints of every SPL model this workspace knows.

    \b
    Examples:
      splent check:contracts
      splent check:contracts --spl cms_spl
    """
    workspace = str(context.workspace())

    if spl_name:
        spl_names = [spl_name]
    else:
        spl_names = spl_store.known_spls(workspace)

    if not spl_names:
        click.secho("  No SPL models known to this workspace.", fg="red")
        click.echo("  Start one with: splent spl:create <name>")
        raise SystemExit(1)

    total_errors = 0
    total_warnings = 0

    for name in spl_names:
        spl_errors = 0
        spl_warnings = 0
        pin = spl_store.read_pin(workspace, name)
        uvl_path_str = spl_store.find_uvl(
            workspace, name, version=pin.version, doi=pin.doi
        )
        click.echo()
        click.echo(click.style(f"  check:contracts  {name}", bold=True))
        click.echo(click.style(f"  {'-' * 60}", fg="bright_black"))

        if not uvl_path_str:
            if pin.fetchable:
                click.secho(
                    f"  UVL not on disk, run: splent spl:fetch {name}",
                    fg="yellow",
                )
            else:
                click.secho(
                    f"  No UVL and no DOI for '{name}', nothing to check against.",
                    fg="yellow",
                )
            total_warnings += 1
            continue

        uvl_path = Path(uvl_path_str)
        model = marketplace.parse_uvl_structure(uvl_path.read_text())
        shorts = model["features"]  # short -> {package, org, ...}
        implications = {(a, b) for a, b in model["constraints"]}

        contracts: dict[str, list[str] | None] = {}
        for short, meta in shorts.items():
            contracts[short] = _local_contract_requires(workspace, meta["package"])

        missing_local = sorted(s for s, req in contracts.items() if req is None)

        # 1. Contract deps that the UVL does not enforce.
        for short, req in sorted(contracts.items()):
            for dep in (req or {}).get("features", []):
                if dep not in shorts:
                    # Dependency on a feature outside this SPL: the SPL simply
                    # cannot offer this feature safely without it.
                    click.secho(
                        f"  ✗ {short} requires '{dep}' (contract), but '{dep}' "
                        f"is not a feature of {name}",
                        fg="red",
                    )
                    spl_errors += 1
                elif (short, dep) not in implications:
                    click.secho(
                        f"  ✗ {short} requires '{dep}' (contract), but the UVL "
                        f"has no '{short} => {dep}' constraint",
                        fg="red",
                    )
                    click.echo(
                        click.style(
                            f"      fix: add '{short} => {dep}' to the UVL "
                            f"constraints (splent spl:add-constraints "
                            f"{name} is interactive)",
                            fg="bright_black",
                        )
                    )
                    spl_errors += 1

        # 2. UVL implications with no contract behind them.
        for a, b in sorted(implications):
            req = contracts.get(a)
            if a not in shorts or b not in shorts:
                continue  # constraint over infra features (redis, nginx…)
            if req is None:
                continue  # feature not local — cannot verify
            if b in req["optional"]:
                continue  # SPL hardening a soft (graceful) dependency — fine
            if b not in req["features"]:
                click.secho(
                    f"  ⚠  UVL says '{a} => {b}' but {a}'s contract does not "
                    f"require '{b}'",
                    fg="yellow",
                )
                click.echo(
                    click.style(
                        f'      fix: add "{b}" to requires.features_manual in '
                        f"{shorts[a]['package']}/pyproject.toml (preserved on "
                        "regeneration), or confirm it is SPL-level policy",
                        fg="bright_black",
                    )
                )
                spl_warnings += 1

        if missing_local:
            click.echo(
                click.style(
                    f"  ℹ  not local (skipped): {', '.join(missing_local)}",
                    fg="bright_black",
                )
            )

        if not spl_errors and not spl_warnings:
            click.secho("  ✅ Contracts and UVL agree.", fg="green")
        total_errors += spl_errors
        total_warnings += spl_warnings

    click.echo()
    if total_errors or total_warnings:
        click.echo(
            click.style(
                f"  {total_errors} error(s), {total_warnings} warning(s).",
                bold=True,
            )
        )
        click.echo()
    if total_errors:
        raise SystemExit(1)


cli_command = check_contracts
