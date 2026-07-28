import click

from splent_cli.services import registry, release_gate


@click.command(
    "check:pypi",
    short_help="Verify PyPI credentials from .env (TWINE_USERNAME / TWINE_PASSWORD)",
)
@click.option(
    "--test", is_flag=True, default=False, help="Check against TestPyPI instead of PyPI"
)
def check_pypi(test: bool):
    click.echo(click.style("\nPyPI Credentials Check\n", fg="cyan", bold=True))

    # Exactly the credentials the upload will use, resolved once, so this
    # command and the release gate can never disagree about what is configured.
    resolved_user, resolved_password = release_gate.pypi_credentials()
    username = (resolved_user or "").strip()
    password = (resolved_password or "").strip()

    registry_name = "TestPyPI" if test else "PyPI"

    # --- presence checks ---
    if not username:
        click.echo(click.style("[✖] ", fg="red") + "TWINE_USERNAME not set in .env")
        click.secho(
            "   Run 'splent tokens:setup' for setup instructions.", fg="bright_black"
        )
        raise SystemExit(1)
    click.echo(click.style("[✔] ", fg="green") + f"TWINE_USERNAME = {username}")

    if not password:
        click.echo(
            click.style("[✖] ", fg="red")
            + "TWINE_PASSWORD (or PYPI_PASSWORD) not set in .env"
        )
        click.secho(
            "   Run 'splent tokens:setup' for setup instructions.", fg="bright_black"
        )
        raise SystemExit(1)

    masked = (
        password[:4] + "*" * (len(password) - 8) + password[-4:]
        if len(password) > 8
        else "****"
    )
    click.echo(click.style("[✔] ", fg="green") + f"TWINE_PASSWORD = {masked}")

    if username == "__token__" and not password.startswith("pypi-"):
        click.echo(
            click.style("[⚠] ", fg="yellow")
            + "TWINE_USERNAME is '__token__' but password doesn't start with 'pypi-', may be invalid"
        )
    elif username == "__token__":
        click.echo(
            click.style("[✔] ", fg="green") + "Token format looks correct (pypi-...)"
        )

    # --- verify via the upload endpoint (valid token -> 400, invalid -> 403/401) ---
    # The probe itself lives in services/registry.py, the single PyPI boundary,
    # so the release gate and this command read the same signal from the same
    # request instead of two probes that can disagree.
    click.echo(f"\nContacting {registry_name} API...")
    try:
        probe = registry.pypi_upload_probe(username, password, test=test)
    except registry.RegistryError as e:
        click.echo(click.style("[✖] ", fg="red") + f"{e}")
        raise SystemExit(1)

    if probe.rate_limited:
        click.echo(
            click.style("[✖] ", fg="red")
            + f"{registry_name} is RATE LIMITING this account (HTTP 429)"
        )
        click.secho(f"   {release_gate.pypi_window(probe.retry_after)}", fg="yellow")
        click.secho(
            "   The credentials are not the problem. Uploads will be refused until "
            "the limit clears.",
            fg="bright_black",
        )
        raise SystemExit(1)

    if probe.status in (401, 403):
        click.echo(
            click.style("[✖] ", fg="red")
            + f"Credentials rejected by {registry_name} (HTTP {probe.status}), "
            "token invalid or expired"
        )
        click.secho(
            "   Run 'splent tokens:setup' for instructions on obtaining a valid token.",
            fg="bright_black",
        )
        raise SystemExit(1)

    if probe.status == 400:
        click.echo(
            click.style("[✔] ", fg="green")
            + f"Credentials valid ({registry_name} returned 400, expected for an empty upload)"
        )
    elif 200 <= probe.status < 300:
        click.echo(
            click.style("[✔] ", fg="green") + f"Credentials accepted by {registry_name}"
        )
    else:
        # An answer that is not a verdict is not a pass. The release gate
        # refuses on this exact input, and the command an operator runs to
        # decide whether to release must not be the more permissive of the two.
        click.echo(
            click.style("[✖] ", fg="red")
            + f"{registry_name} returned HTTP {probe.status}, which is not a usable "
            "answer, so the credentials could NOT be confirmed"
        )
        if probe.detail:
            click.secho(f"   {probe.detail}", fg="bright_black")
        click.secho(
            "   Retry once it answers normally. A release would be refused in this "
            "state.",
            fg="yellow",
        )
        raise SystemExit(1)

    click.echo()
    click.secho(f"PyPI credentials OK ({registry_name}).", fg="green")


cli_command = check_pypi
