"""
splent doctor — Orchestrates all diagnostic checks.

Runs each check/info command in sequence and reports a final summary.
Each phase is a standalone command that can also be run independently.
"""
import click

from splent_cli.commands.check.check_env import check_env
from splent_cli.commands.check.check_pyproject import check_pyproject
from splent_cli.commands.check.check_features import check_features
from splent_cli.commands.check.check_docker import check_docker
from splent_cli.commands.check.check_github import check_github
from splent_cli.commands.check.check_pypi import check_pypi
from splent_cli.commands.check.check_deps import check_deps
from splent_cli.commands.feature.feature_status import feature_status
from splent_cli.commands.version import version as version_cmd
from splent_cli.commands.database.db_status import db_status
from splent_cli.commands.product.product_status import product_status


# (name, command, requires_db, network_only)
CHECKS = [
    ("version",          version_cmd,    False, False),
    ("check:env",        check_env,      False, False),
    ("check:pyproject",  check_pyproject, False, False),
    ("check:features",   check_features, False, False),
    ("check:deps",       check_deps,     False, False),
    ("feature:status",   feature_status, False, False),
    ("check:docker",     check_docker,   False, False),
    ("product:status",   product_status, False, False),
    ("db:status",        db_status,      True,  False),
    ("check:github",     check_github,   False, True),
    ("check:pypi",       check_pypi,     False, True),
]


@click.command("doctor", short_help="Run all diagnostic checks on the workspace.")
@click.option("--fast", is_flag=True, help="Skip slow checks (network, database).")
def doctor(fast):
    """
    Orchestrates all diagnostic commands and reports a summary.

    \b
    Runs in order:
      1. version           — Workspace version snapshot
      2. check:env         — Python, env vars, CLI/framework versions
      3. check:pyproject   — pyproject.toml parsing, deps, UVL
      4. check:features    — cache, symlinks, pip install, git state
      5. feature:status    — lifecycle state of all features
      6. check:docker      — Docker daemon, compose, containers
      7. product:status    — Docker container status for the product
      8. db:status         — Migration status for all features
      9. check:github      — GitHub credentials and API access
     10. check:pypi        — PyPI credentials and upload access

    \b
    Each is a standalone command you can run independently:
      splent check:env
      splent feature:status
      splent db:status
      etc.

    \b
    Use --fast to skip network checks (GitHub, PyPI) and database checks.
    """
    skip = set()
    if fast:
        skip = {name for name, _, _, network in CHECKS if network}
        skip |= {name for name, _, db, _ in CHECKS if db}

    click.echo(click.style("\n🩺 SPLENT Doctor\n", fg="cyan", bold=True))

    passed = 0
    failed = 0
    skipped = 0

    for name, cmd, requires_db_flag, network_flag in CHECKS:
        if name in skip:
            click.echo(click.style(f"━━ {name}", fg="bright_black") +
                       click.style(" (skipped)", fg="bright_black"))
            skipped += 1
            click.echo()
            continue

        click.echo(click.style(f"━━ {name}", fg="cyan", bold=True))

        try:
            ctx = click.Context(cmd, info_name=name, parent=click.get_current_context())
            if requires_db_flag:
                from splent_cli.utils.dynamic_imports import get_app
                app = get_app()
                with app.app_context():
                    ctx.invoke(cmd)
            else:
                ctx.invoke(cmd)
            passed += 1
        except SystemExit as e:
            if e.code and e.code != 0:
                failed += 1
            else:
                passed += 1
        except Exception as e:
            click.secho(f"  Unexpected error: {e}", fg="red")
            failed += 1

        click.echo()

    # Summary
    click.echo(click.style("━━ Summary", fg="cyan", bold=True))
    click.echo(f"  ✔ Passed: {passed}")
    if skipped:
        click.echo(f"  ⏩ Skipped: {skipped}")
    if failed:
        click.echo(f"  ✖ Failed: {failed}")
        click.echo()
        click.secho("  Some checks failed. Review the output above.", fg="red")
        raise SystemExit(1)
    else:
        click.echo()
        click.secho("  ✅ All checks passed.", fg="green")

    click.echo()


cli_command = doctor
