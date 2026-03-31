"""
splent feature:pin

Pin all editable features to their latest cached version.

After running ``feature:release`` for editable features, this command updates
the product's pyproject.toml to reference the latest version found in
``.splent_cache/`` — turning editable entries into pinned ones.
"""

import os
import re
import tomllib

import click
import tomli_w

from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    parse_feature_entry,
    write_features_to_data,
)


def _latest_cached_version(cache_base: str, feature_name: str) -> str | None:
    """Find the highest semver tag for *feature_name* in the cache directory."""
    if not os.path.isdir(cache_base):
        return None

    versions = []
    prefix = f"{feature_name}@"
    for entry in os.listdir(cache_base):
        if entry.startswith(prefix):
            tag = entry[len(prefix) :]
            m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", tag)
            if m:
                versions.append(
                    (int(m.group(1)), int(m.group(2)), int(m.group(3)), tag)
                )

    if not versions:
        return None

    versions.sort(reverse=True)
    return versions[0][3]  # tag string of highest version


@click.command(
    "feature:pin",
    short_help="Pin editable features to their latest cached version.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would change without modifying pyproject.toml.",
)
@context.requires_product
def feature_pin(dry_run):
    """Pin all editable features to their latest released version in cache.

    \b
    For each editable feature (no @version) declared in pyproject.toml,
    looks up the highest version in .splent_cache/ and updates the entry.

    \b
    Examples:
      splent feature:pin             # pin all editables
      splent feature:pin --dry-run   # preview without changes
    """
    workspace = str(context.workspace())
    product = context.require_app()
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    click.echo()
    click.secho("  feature:pin", fg="cyan", bold=True)
    click.echo()

    pinned_count = 0

    for features_key in ("features", "features_dev", "features_prod"):
        features = data.get("tool", {}).get("splent", {}).get(features_key, [])
        if not features:
            continue

        updated = []
        for entry in features:
            ns_safe, name, version = parse_feature_entry(entry)

            if version:
                # Already pinned — keep as-is
                updated.append(entry)
                continue

            # Editable — look for latest in cache
            cache_base = os.path.join(workspace, ".splent_cache", "features", ns_safe)
            latest = _latest_cached_version(cache_base, name)

            if not latest:
                short = name.removeprefix("splent_feature_")
                click.secho(
                    f"  ⚠  {short:<20} no cached version found — skipping", fg="yellow"
                )
                updated.append(entry)
                continue

            # Reconstruct with original namespace format
            ns_raw = entry.split("/", 1)[0] if "/" in entry else "splent-io"
            new_entry = f"{ns_raw}/{name}@{latest}"
            updated.append(new_entry)

            short = name.removeprefix("splent_feature_")
            click.secho(f"  ✓  {short:<20} → {latest}", fg="green")
            pinned_count += 1

        if updated != features:
            write_features_to_data(data, updated, key=features_key)

    if pinned_count == 0:
        click.secho("  No editable features to pin.", dim=True)
        click.echo()
        return

    if dry_run:
        click.echo()
        click.secho(
            f"  Dry run — {pinned_count} feature(s) would be pinned.", fg="yellow"
        )
        click.echo()
        return

    with open(pyproject_path, "wb") as f:
        tomli_w.dump(data, f)

    click.echo()
    click.secho(f"  ✅ Pinned {pinned_count} feature(s) in pyproject.toml.", fg="green")
    click.secho("  Run 'splent product:sync' to update symlinks.", dim=True)
    click.echo()


cli_command = feature_pin
