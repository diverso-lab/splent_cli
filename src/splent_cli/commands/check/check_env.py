"""
check:env — Validate workspace environment variables and tool versions.
"""
import os
import sys

import click
import importlib.metadata


def _pkg_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except Exception:
        return None


@click.command("check:env", short_help="Validate workspace environment and tool versions.")
def check_env():
    """Check Python version, SPLENT env vars, CLI/framework compatibility."""
    ok = fail = warn = 0

    def _ok(msg):
        nonlocal ok; ok += 1
        click.echo(click.style("  [✔] ", fg="green") + msg)

    def _fail(msg):
        nonlocal fail; fail += 1
        click.echo(click.style("  [✖] ", fg="red") + msg)

    def _warn(msg):
        nonlocal warn; warn += 1
        click.echo(click.style("  [⚠] ", fg="yellow") + msg)

    click.echo()

    # Python
    _ok(f"Python {sys.version.split()[0]}")

    # WORKING_DIR
    wd = os.getenv("WORKING_DIR")
    if wd:
        _ok(f"WORKING_DIR = {wd}")
    else:
        _warn("WORKING_DIR not set (defaulting to cwd)")

    # .env file
    workspace = wd or os.getcwd()
    if os.path.exists(os.path.join(workspace, ".env")):
        _ok(".env file found")
    else:
        _warn(".env file not found in workspace")

    # SPLENT_APP
    app = os.getenv("SPLENT_APP")
    if app:
        app_path = os.path.join(workspace, app)
        if os.path.isdir(app_path):
            _ok(f"SPLENT_APP = {app}")
        else:
            _fail(f"SPLENT_APP = {app} (directory not found)")
    else:
        _fail("SPLENT_APP not set")

    # SPLENT_ENV
    env = os.getenv("SPLENT_ENV")
    if env:
        _ok(f"SPLENT_ENV = {env}")
    else:
        _warn("SPLENT_ENV not set (defaults to dev)")

    # CLI / Framework versions
    cli_v = _pkg_version("splent_cli")
    fw_v = _pkg_version("splent_framework")
    if cli_v and fw_v:
        if cli_v.split(".")[0] == fw_v.split(".")[0]:
            _ok(f"CLI {cli_v} / Framework {fw_v} — compatible")
        else:
            _fail(f"CLI {cli_v} / Framework {fw_v} — major version mismatch")
    else:
        _fail(f"CLI={'?' if not cli_v else cli_v} / Framework={'?' if not fw_v else fw_v}")

    # Credentials
    if os.getenv("GITHUB_TOKEN"):
        _ok("GITHUB_TOKEN set")
    else:
        _warn("GITHUB_TOKEN not set")

    if os.getenv("TWINE_PASSWORD") or os.getenv("PYPI_TOKEN"):
        _ok("PyPI token set")
    else:
        _warn("PyPI token not set")

    click.echo()
    if fail:
        click.secho(f"  {fail} check(s) failed.", fg="red")
        raise SystemExit(1)


cli_command = check_env
