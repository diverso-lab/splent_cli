"""
feature:pip-install installs the declared features from PyPI.

Reads [tool.splent].features from the active product's pyproject.toml and
installs each entry as a PyPI package. This is what the production image runs;
scripts/00_install_features.sh, which installs the local checkout with
pip install -e, is the development path and never runs here.

Two things about this are worth stating plainly rather than discovering:

  * only [tool.splent].features is read. features_dev and features_prod are
    NOT installed by this command, so a feature declared only under
    features_prod is merged into the deploy env and compose by product:build
    and then never installed into the image.
  * an entry without @version becomes a bare package name, so pip resolves it
    against PyPI and installs whatever is published there. It does not fail,
    and it does not install the workspace checkout.

Example:
    splent-io/splent_feature_auth@v1.2.7  ->  pip install splent_feature_auth==1.2.7
    splent-io/splent_feature_auth         ->  pip install splent_feature_auth
"""

import os
import sys

import click
import tomllib

from splent_cli.services import context
from splent_cli.utils.proc import run


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


def _uninstalled_env_features(splent: dict) -> list[str]:
    """Entries this command will not install because of where they are declared.

    features_dev / features_prod are read by product:build and by
    product:resolve, so it is easy to assume they are read here too. They are
    not, and a feature that is merged into the deploy artifacts but never
    installed into the image fails at import time with nothing pointing back
    at the declaration.
    """
    base = set(splent.get("features", []) or [])
    extra = []
    for key in ("features_prod", "features_dev"):
        for entry in splent.get(key, []) or []:
            if entry not in base:
                extra.append(f"{entry}  [{key}]")
    return extra


@click.command(
    "feature:pip-install",
    short_help="Install the declared features from PyPI.",
)
def feature_pip_install():
    """Install features declared in [tool.splent].features from PyPI.

    Used in production Dockerfiles, where features are installed as published
    packages rather than from local source. Only [tool.splent].features is
    read; anything declared solely under features_dev or features_prod is
    reported and left alone.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")

    if not os.path.isfile(pyproject_path):
        click.secho("❌ pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    splent = data.get("tool", {}).get("splent", {})
    features = splent.get("features", [])

    ignored = _uninstalled_env_features(splent)
    if ignored:
        click.secho(
            "  Declared elsewhere and NOT installed by this command:", fg="yellow"
        )
        for entry in ignored:
            click.echo(f"    {entry}")
        click.secho(
            "  Move them to [tool.splent].features if the image needs them.",
            fg="yellow",
        )
        click.echo()

    if not features:
        click.secho("  No features declared in [tool.splent].features.", fg="yellow")
        return

    click.echo(f"  Installing {len(features)} feature(s) from PyPI...\n")

    failed = []
    unpinned = []
    for entry in features:
        name, version = _parse_feature_entry(entry)
        spec = f"{name}=={version}" if version else name
        if not version:
            unpinned.append(name)

        click.echo(f"  installing {spec}")

        result = run(
            [sys.executable, "-m", "pip", "install", "--no-cache-dir", spec],
            check=False,
            capture=True,
        )

        if result.returncode == 0:
            click.echo(f"  ok {spec}")
        else:
            output = (result.stderr or result.stdout or "").strip()
            last_line = output.splitlines()[-1] if output else "no output"
            click.secho(f"  FAIL {spec}: {last_line}", fg="red")
            failed.append(spec)

    click.echo()
    if unpinned:
        click.secho(
            "  Installed without a version, so pip chose what PyPI serves and "
            "not any local checkout:",
            fg="yellow",
        )
        for name in unpinned:
            click.echo(f"    {name}")
        click.echo()
    if failed:
        click.secho(f"  {len(failed)} feature(s) failed to install:", fg="red")
        for f in failed:
            click.echo(f"    {f}")
        raise SystemExit(1)
    else:
        click.secho(
            f"  All {len(features)} feature(s) installed from PyPI.", fg="green"
        )


cli_command = feature_pip_install
