import os
import urllib.request
import urllib.error
import json
import click


@click.command(
    "check:github",
    short_help="Verify GitHub credentials from .env (GITHUB_USER / GITHUB_TOKEN)",
)
def check_github():
    click.echo(click.style("\n🐙 GitHub Credentials Check\n", fg="cyan", bold=True))

    user = os.getenv("GITHUB_USER", "").strip()
    token = os.getenv("GITHUB_TOKEN", "").strip()

    # --- presence checks ---
    if not user:
        click.echo(click.style("[✖] ", fg="red") + "GITHUB_USER not set in .env")
        raise SystemExit(1)
    click.echo(click.style("[✔] ", fg="green") + f"GITHUB_USER = {user}")

    if not token:
        click.echo(click.style("[✖] ", fg="red") + "GITHUB_TOKEN not set in .env")
        raise SystemExit(1)

    masked = token[:4] + "*" * (len(token) - 8) + token[-4:]
    click.echo(click.style("[✔] ", fg="green") + f"GITHUB_TOKEN = {masked}")

    # --- API call ---
    click.echo("\nContacting GitHub API...")
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "splent-cli",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            rate_remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            rate_limit = resp.headers.get("X-RateLimit-Limit", "?")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            click.echo(
                click.style("[✖] ", fg="red")
                + "Token invalid or expired (401 Unauthorized)"
            )
        elif e.code == 403:
            click.echo(
                click.style("[✖] ", fg="red")
                + "Token lacks required permissions (403 Forbidden)"
            )
        else:
            click.echo(
                click.style("[✖] ", fg="red") + f"GitHub API error: HTTP {e.code}"
            )
        raise SystemExit(1)
    except urllib.error.URLError as e:
        click.echo(click.style("[✖] ", fg="red") + f"Network error: {e.reason}")
        raise SystemExit(1)

    # --- validate identity ---
    api_login = data.get("login", "")
    if api_login.lower() != user.lower():
        click.echo(
            click.style("[⚠] ", fg="yellow")
            + f"Token belongs to '{api_login}', but GITHUB_USER is '{user}'"
        )
    else:
        click.echo(click.style("[✔] ", fg="green") + f"Authenticated as {api_login}")

    name = data.get("name") or "-"
    plan = (data.get("plan") or {}).get("name", "-")
    click.echo(click.style("[✔] ", fg="green") + f"Name: {name}")
    click.echo(click.style("[✔] ", fg="green") + f"Plan: {plan}")
    click.echo(
        click.style("[✔] ", fg="green")
        + f"Rate limit: {rate_remaining}/{rate_limit} remaining"
    )

    click.echo()
    click.secho("✅ GitHub credentials OK.", fg="green")


cli_command = check_github
