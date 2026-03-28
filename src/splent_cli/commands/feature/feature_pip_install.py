"""
feature:pip-install — Install all declared features from PyPI.

Reads [tool.splent].features from the active product's pyproject.toml
and installs each pinned feature as a PyPI package.

This is the production equivalent of the development workflow where
features are installed from local symlinks via pip install -e.

Example:
    splent-io/splent_feature_auth@v1.2.7  →  pip install splent_feature_auth==1.2.7
"""

import os
import subprocess
import sys

import click
import tomllib

from splent_cli.services import context


def _parse_feature_entry(entry: str) -> tuple[str, str | None]:
    """Parse a [tool.splent].features entry into (package_name, version).

    'splent-io/splent_feature_auth@v1.2.7' → ('splent_feature_auth', '1.2.7')
    'splent-io/splent_feature_auth'         → ('splent_feature_auth', None)
    'splent_feature_auth@v1.2.7'            → ('splent_feature_auth', '1.2.7')
    """
    # Strip namespace
    name_ver = entry.split("/")[-1] if "/" in entry else entry
    name, _, version = name_ver.partition("@")
    version = version.lstrip("v") if version else None
    return name, version


@click.command(
    "feature:pip-install",
    short_help="Install all declared features from PyPI.",
)
def feature_pip_install():
    """Install features declared in [tool.splent].features from PyPI.

    Used in production Dockerfiles where features are installed as
    pre-built packages instead of from local source code.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")

    if not os.path.isfile(pyproject_path):
        click.secho("❌ pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    features = data.get("tool", {}).get("splent", {}).get("features", [])

    if not features:
        click.secho("ℹ️  No features declared in [tool.splent].features.", fg="yellow")
        return

    click.echo(f"📦 Installing {len(features)} feature(s) from PyPI...\n")

    failed = []
    for entry in features:
        name, version = _parse_feature_entry(entry)
        spec = f"{name}=={version}" if version else name

        click.echo(f"  ⬇️  {spec}")

        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", spec],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            click.echo(f"  ✅ {spec}")
        else:
            click.secho(f"  ❌ {spec}: {result.stderr.strip().splitlines()[-1]}", fg="red")
            failed.append(spec)

    click.echo()
    if failed:
        click.secho(f"❌ {len(failed)} feature(s) failed to install:", fg="red")
        for f in failed:
            click.echo(f"   - {f}")
        raise SystemExit(1)
    else:
        click.secho(f"✅ All {len(features)} feature(s) installed from PyPI.", fg="green")


cli_command = feature_pip_install
