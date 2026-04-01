import click


@click.command(
    "tokens",
    short_help="Show how to obtain and configure GitHub and PyPI tokens.",
)
def tokens():
    """
    Show instructions for obtaining and configuring the tokens required
    by splent feature:release, check:github and check:pypi.
    """
    click.echo()
    click.secho("  Required tokens for SPLENT", bold=True)
    click.echo(click.style(f"  {'─' * 56}", fg="bright_black"))

    # ── GitHub ────────────────────────────────────────────────────────
    click.echo()
    click.secho("  GITHUB_TOKEN", fg="cyan", bold=True)
    click.echo("  Used by: feature:release, feature:versions, feature:upgrade,")
    click.echo("           feature:search, check:github")
    click.echo()
    click.echo("  How to get it:")
    click.echo("    1. Go to github.com → Settings → Developer settings")
    click.echo("       → Personal access tokens → Tokens (classic)")
    click.echo("    2. Generate new token (classic)")
    click.echo("    3. Enable scope: repo  (full control of private repos)")
    click.echo("    4. Copy the token (shown only once)")
    click.echo()
    click.secho("  Add to your .env:", fg="bright_black")
    click.echo("    GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxx")

    # ── PyPI ──────────────────────────────────────────────────────────
    click.echo()
    click.echo(click.style(f"  {'─' * 56}", fg="bright_black"))
    click.echo()
    click.secho("  TWINE_USERNAME + TWINE_PASSWORD", fg="cyan", bold=True)
    click.echo("  Used by: feature:release, check:pypi")
    click.echo()
    click.echo("  How to get it:")
    click.echo("    1. Go to pypi.org → Account settings → API tokens")
    click.echo("    2. Add API token")
    click.echo("    3. Scope: 'Entire account' or limit to a specific project")
    click.echo("    4. Copy the token (shown only once)")
    click.echo()
    click.secho("  Add to your .env:", fg="bright_black")
    click.echo("    TWINE_USERNAME=__token__")
    click.echo("    TWINE_PASSWORD=pypi-xxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    click.echo()
    click.secho(
        "  Note: TWINE_USERNAME is always the literal string __token__,",
        fg="bright_black",
    )
    click.secho(
        "        not your PyPI username.",
        fg="bright_black",
    )

    # ── Verify ────────────────────────────────────────────────────────
    click.echo()
    click.echo(click.style(f"  {'─' * 56}", fg="bright_black"))
    click.echo()
    click.secho("  After adding tokens to .env, reload them:", bold=True)
    click.echo("    source .env")
    click.echo()
    click.secho("  Then verify:", bold=True)
    click.echo("    splent check:github")
    click.echo("    splent check:pypi")
    click.echo()


cli_command = tokens
