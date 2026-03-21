import os
import urllib.request
import urllib.error
import base64
import click


@click.command(
    "check:pypi",
    short_help="Verify PyPI credentials from .env (TWINE_USERNAME / TWINE_PASSWORD)",
)
@click.option("--test", is_flag=True, default=False, help="Check against TestPyPI instead of PyPI")
def check_pypi(test: bool):
    click.echo(click.style("\n📦 PyPI Credentials Check\n", fg="cyan", bold=True))

    username = os.getenv("TWINE_USERNAME", "").strip()
    password = os.getenv("TWINE_PASSWORD", os.getenv("PYPI_PASSWORD", "")).strip()

    registry = "TestPyPI" if test else "PyPI"
    upload_url = "https://test.pypi.org/legacy/" if test else "https://upload.pypi.org/legacy/"

    # --- presence checks ---
    if not username:
        click.echo(click.style("[✖] ", fg="red") + "TWINE_USERNAME not set in .env")
        raise SystemExit(1)
    click.echo(click.style("[✔] ", fg="green") + f"TWINE_USERNAME = {username}")

    if not password:
        click.echo(click.style("[✖] ", fg="red") + "TWINE_PASSWORD (or PYPI_PASSWORD) not set in .env")
        raise SystemExit(1)

    masked = password[:4] + "*" * (len(password) - 8) + password[-4:] if len(password) > 8 else "****"
    click.echo(click.style("[✔] ", fg="green") + f"TWINE_PASSWORD = {masked}")

    if username == "__token__" and not password.startswith("pypi-"):
        click.echo(click.style("[⚠] ", fg="yellow") + "TWINE_USERNAME is '__token__' but password doesn't start with 'pypi-' — may be invalid")
    elif username == "__token__":
        click.echo(click.style("[✔] ", fg="green") + "Token format looks correct (pypi-...)")

    # --- verify via upload endpoint (valid token → 400, invalid → 403/401) ---
    click.echo(f"\nContacting {registry} API...")
    boundary = "splentclicheck"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name=":action"\r\n\r\n'
        f"file_upload\r\n"
        f"--{boundary}--\r\n"
    ).encode()
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()

    req = urllib.request.Request(
        upload_url,
        data=body,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "splent-cli",
        },
        method="POST",
    )

    try:
        urllib.request.urlopen(req, timeout=10)
        click.echo(click.style("[✔] ", fg="green") + f"Credentials accepted by {registry}")
    except urllib.error.HTTPError as e:
        if e.code == 400:
            click.echo(click.style("[✔] ", fg="green") + f"Credentials valid ({registry} returned 400 — expected for empty upload)")
        elif e.code in (401, 403):
            click.echo(click.style("[✖] ", fg="red") + f"Credentials rejected by {registry} (HTTP {e.code}) — token invalid or expired")
            raise SystemExit(1)
        else:
            click.echo(click.style("[⚠] ", fg="yellow") + f"{registry} returned HTTP {e.code} — credentials may be OK but could not confirm")
    except urllib.error.URLError as e:
        click.echo(click.style("[✖] ", fg="red") + f"Network error: {e.reason}")
        raise SystemExit(1)

    click.echo()
    click.secho(f"✅ PyPI credentials OK ({registry}).", fg="green")


cli_command = check_pypi
