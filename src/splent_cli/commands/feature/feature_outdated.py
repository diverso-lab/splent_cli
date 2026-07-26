"""
splent feature:outdated

Check if any pinned features have newer versions available on GitHub.
"""

import os
import re

import click

from splent_cli.services import context, registry
from splent_cli.utils.feature_utils import parse_feature_entry
from splent_cli.utils.io_utils import load_toml
from splent_cli.utils.proc import run


def _fetch_latest_tag(namespace: str, repo: str) -> str | None:
    """Fetch the latest semver tag from GitHub, sorted properly."""
    tags = registry.list_semver_tags(
        namespace, repo, token=registry.github_token(), limit=1
    )
    return tags[0] if tags else None


def _parse_semver(tag: str) -> tuple[int, ...] | None:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", tag)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


@click.command(
    "feature:outdated",
    short_help="Check GitHub for newer versions of pinned features.",
)
@click.option(
    "--upgrade",
    is_flag=True,
    help="Upgrade all outdated features to their latest version.",
)
@context.requires_product
def feature_outdated(upgrade):
    """Compare pinned feature versions against the latest on GitHub.

    \b
    Shows a table of pinned features with their current and latest versions.

    \b
    Use --upgrade to automatically update all outdated features:
      splent feature:outdated --upgrade
    """
    workspace = str(context.workspace())
    product = context.require_app()
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")

    data = load_toml(pyproject_path, what="product pyproject.toml")

    from splent_cli.utils.feature_utils import read_features_from_data

    env = os.getenv("SPLENT_ENV")
    features = read_features_from_data(data, env)

    # Only pinned features (with @version)
    pinned = []
    for entry in features:
        ns_safe, name, version = parse_feature_entry(entry)
        if version:
            ns_raw = entry.split("/", 1)[0] if "/" in entry else "splent-io"
            pinned.append((ns_raw, ns_safe, name, version, entry))

    if not pinned:
        click.echo()
        click.secho("  No pinned features to check.", fg="yellow")
        click.echo()
        return

    click.echo()
    click.secho("  feature:outdated", fg="cyan", bold=True)
    click.echo()
    click.echo(f"  Checking {len(pinned)} pinned feature(s) against GitHub...")
    click.echo()

    # Column widths
    shorts = [n.removeprefix("splent_feature_") for _, _, n, _, _ in pinned]
    col_name = max(len(s) for s in shorts)
    col_name = max(col_name, len("Feature"))
    col_ver = 12

    click.echo(
        f"  {'Feature':<{col_name}}  {'Current':<{col_ver}}  {'Latest':<{col_ver}}  Status"
    )
    click.echo(f"  {'-' * col_name}  {'-' * col_ver}  {'-' * col_ver}  {'-' * 12}")

    to_upgrade = []
    for ns_raw, ns_safe, name, current, entry in pinned:
        short = name.removeprefix("splent_feature_")
        latest = _fetch_latest_tag(ns_raw, name)

        if not latest:
            status = click.style("? unreachable", fg="yellow")
            latest_col = f"{'—':<{col_ver}}"
        else:
            cur_sem = _parse_semver(current)
            lat_sem = _parse_semver(latest)

            if cur_sem and lat_sem and lat_sem > cur_sem:
                status = click.style("⬆ update", fg="green", bold=True)
                latest_col = f"{click.style(latest, fg='green'):<{col_ver + 9}}"
                to_upgrade.append((ns_raw, name, latest))
            else:
                status = click.style("✔ latest", fg="bright_black")
                latest_col = f"{latest:<{col_ver}}"

        click.echo(
            f"  {short:<{col_name}}  {current:<{col_ver}}  {latest_col}  {status}"
        )

    click.echo()

    if not to_upgrade:
        click.secho("  All features are up to date.", fg="green")
        click.echo()
        return

    click.secho(
        f"  {len(to_upgrade)} feature(s) can be updated.", fg="green", bold=True
    )
    click.echo()

    if not upgrade:
        click.secho(
            "  splent feature:upgrade <feature>            update one", dim=True
        )
        click.secho(
            "  splent feature:outdated --upgrade           update all", dim=True
        )
        click.secho(
            "  splent cache:prune                          clean old cached versions",
            dim=True,
        )
        click.echo()
        return

    # Upgrade all outdated features via feature:upgrade --yes
    click.secho("  Upgrading...", bold=True)
    click.echo()
    upgraded = 0
    for ns_raw, name, version in to_upgrade:
        short = name.removeprefix("splent_feature_")
        click.echo(f"  ⬆  {short} → {version}")
        result = run(
            ["splent", "feature:upgrade", f"{ns_raw}/{name}", "--yes"],
            check=False,
        )
        if result.returncode == 0:
            upgraded += 1
        else:
            click.secho(f"  ❌ Failed to upgrade {short}", fg="red")

    click.echo()
    if upgraded:
        click.secho(f"  ✅ Upgraded {upgraded} feature(s).", fg="green")
        click.secho("  Run 'splent product:resolve' to update symlinks.", dim=True)
    click.echo()


cli_command = feature_outdated
