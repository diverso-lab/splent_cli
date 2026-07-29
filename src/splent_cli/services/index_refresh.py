"""Ask the marketplace index to rebuild itself.

The published index is what the marketplace serves, and it is built by a
GitHub Actions workflow that runs on a push to its own repository, on a
schedule, or on demand. A feature release or an SPL publication happens in
a different repository, so neither of them used to reach it: the index
caught up on the next cron run, hours later, and the only way to see a
release in production sooner was to press the button by hand.

Releasing now asks for the rebuild as its last step. It is deliberately
best effort: the release itself is already done and irreversible by then,
so a token without workflow scope, or a network that is down, is reported
and never turns a finished release into a failure.
"""

import json
import os
import urllib.error
import urllib.request

import click

INDEX_REPO = os.getenv("SPLENT_INDEX_REPO", "splent-io/splent_index")
INDEX_WORKFLOW = os.getenv("SPLENT_INDEX_WORKFLOW", "build-index.yml")
INDEX_REF = os.getenv("SPLENT_INDEX_REF", "main")
TIMEOUT_SECONDS = 15


def _api_url() -> str:
    return (
        f"https://api.github.com/repos/{INDEX_REPO}"
        f"/actions/workflows/{INDEX_WORKFLOW}/dispatches"
    )


def request_rebuild(*, quiet: bool = False) -> bool:
    """Trigger the index build workflow. True when GitHub accepted it.

    Never raises: the caller has already published something.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        if not quiet:
            click.secho(
                "  index    no GITHUB_TOKEN, so the marketplace index was not "
                "asked to rebuild. It will catch up on its own schedule.",
                fg="yellow",
            )
        return False

    request = urllib.request.Request(
        _api_url(),
        data=json.dumps({"ref": INDEX_REF}).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "splent-cli",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            accepted = response.status in (200, 201, 204)
    except urllib.error.HTTPError as error:
        if not quiet:
            hint = (
                " The token needs the workflow scope."
                if error.code in (403, 404)
                else ""
            )
            click.secho(
                f"  index    the marketplace index refused the rebuild "
                f"({error.code}).{hint} It will catch up on its own schedule.",
                fg="yellow",
            )
        return False
    except Exception:
        if not quiet:
            click.secho(
                "  index    could not reach GitHub to rebuild the marketplace "
                "index. It will catch up on its own schedule.",
                fg="yellow",
            )
        return False

    if accepted and not quiet:
        click.secho(
            "  index    marketplace index rebuild requested, so this version "
            "appears there within a couple of minutes.",
            fg="bright_black",
        )
    return accepted
