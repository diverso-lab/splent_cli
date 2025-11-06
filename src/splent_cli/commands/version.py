import os
import json
import click
import importlib.metadata


def _pkg_version(name: str) -> str | None:
    """Return the installed version of a package, or None if not found."""
    if not name:
        return None
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


@click.command("version", help="Show SPLENT and environment version information")
@click.option("--json", "as_json", is_flag=True, help="Output in JSON format")
def version(as_json: bool) -> None:
    """
    Displays the version of the SPLENT CLI, framework, active app, and Python.

    Example:
        $ splent version
        SPLENT CLI 0.8.0
        Framework: 0.8.0
        App: uvlhub 1.2.0
        Python: 3.12.6
    """
    try:
        cli_v = _pkg_version("splent_cli") or "unknown"
        fw_v = _pkg_version("splent_framework") or "unknown"

        app_name = os.getenv("SPLENT_APP")
        app_v = _pkg_version(app_name) if app_name else None
        py_v = os.sys.version.split()[0]

        if as_json:
            payload = {
                "cli": cli_v,
                "framework": fw_v,
                "app": {"name": app_name, "version": app_v} if app_name else None,
                "python": py_v,
            }
            click.echo(json.dumps(payload, ensure_ascii=False))
            return

        click.echo(f"CLI version: {cli_v}")
        click.echo(f"Framework version: {fw_v}")
        if app_name:
            click.echo(f"Active product: {app_name} {app_v or '(not installed)'}")
        else:
            click.echo("App: (not selected)")
        click.echo(f"Python: {py_v}")

    except Exception as exc:
        click.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        raise SystemExit(2)
