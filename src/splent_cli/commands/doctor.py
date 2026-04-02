"""
splent doctor — Run all diagnostic checks on the workspace.

Organized in sections: environment, product, services, and credentials.
"""

import click

from splent_cli.commands.check.check_env import check_env
from splent_cli.commands.check.check_pyproject import check_pyproject
from splent_cli.commands.check.check_features import check_features
from splent_cli.commands.check.check_docker import check_docker
from splent_cli.commands.check.check_github import check_github
from splent_cli.commands.check.check_pypi import check_pypi
from splent_cli.commands.check.check_infra import check_infra
from splent_cli.commands.check.check_product import check_product
from splent_cli.commands.feature.feature_status import feature_status
from splent_cli.commands.database.db_status import db_status
from splent_cli.commands.product.product_containers import product_docker


SECTIONS = [
    (
        "Environment",
        [
            ("check:env", check_env, False, False),
            ("check:pyproject", check_pyproject, False, False),
            ("check:docker", check_docker, False, False),
            ("check:infra", check_infra, False, False),
        ],
    ),
    (
        "Product",
        [
            ("check:product", check_product, False, False),
            ("check:features", check_features, False, False),
            ("feature:status", feature_status, False, False),
        ],
    ),
    (
        "Services",
        [
            ("product:containers", product_docker, False, False),
            ("db:status", db_status, True, False),
        ],
    ),
    (
        "Credentials",
        [
            ("check:github", check_github, False, True),
            ("check:pypi", check_pypi, False, True),
        ],
    ),
]


@click.command(
    "doctor", short_help="Run all diagnostic checks sequentially and report a summary."
)
@click.option("--fast", is_flag=True, help="Skip slow checks (network, database).")
def doctor(fast):
    """
    Run all diagnostic checks on the workspace and report a summary.

    \b
    Sections:
      Environment   — Python, env vars, pyproject, Docker, infrastructure
      Product       — product health, feature cache/symlinks, feature status
      Services      — containers, database migrations
      Credentials   — GitHub and PyPI tokens (skipped with --fast)

    \b
    Use --fast to skip network checks (GitHub, PyPI) and database checks.

    \b
    For full product validation (UVL + contracts + imports), run:
      splent product:validate
    """
    click.echo(click.style("\n  SPLENT Doctor\n", fg="cyan", bold=True))

    passed = 0
    failed = 0
    skipped = 0

    for section_name, checks in SECTIONS:
        click.echo(click.style(f"  ── {section_name} ──", fg="cyan", bold=True))
        click.echo()

        for name, cmd, requires_db, network_only in checks:
            if fast and (network_only or requires_db):
                click.echo(
                    click.style(f"  {name}", fg="bright_black")
                    + click.style("  (skipped)", fg="bright_black")
                )
                skipped += 1
                click.echo()
                continue

            click.echo(click.style(f"  {name}", bold=True))

            try:
                ctx = click.Context(
                    cmd, info_name=name, parent=click.get_current_context()
                )
                if requires_db:
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
    click.echo(click.style("  ── Summary ──", fg="cyan", bold=True))
    click.echo(f"  Passed: {passed}")
    if skipped:
        click.echo(f"  Skipped: {skipped}")
    if failed:
        click.echo(f"  Failed: {failed}")
        click.echo()
        click.secho("  Some checks failed. Review the output above.", fg="red")
        raise SystemExit(1)
    else:
        click.echo()
        click.secho("  All checks passed.", fg="green")

    click.echo()


cli_command = doctor
