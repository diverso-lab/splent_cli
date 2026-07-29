"""Shared plumbing for splent login / logout / whoami.

Keeps the three commands thin and, more importantly, keeps the ERROR MESSAGES
in one place: every failure mode of the marketplace has exactly one wording,
and every wording says what happened and what to do next.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone

import click

from splent_cli.services import credentials, marketplace_api
from splent_cli.services.marketplace_url import (
    LOCAL_CONTAINER_URL,
    LOCAL_HOST_URL,
    REGISTRY_URL_ENV,
    InvalidRegistryURL,
    RegistryTarget,
    resolve_registry,
)

LABEL_WIDTH = 10


# ── Registry / client ─────────────────────────────────────────────────


def resolve_target(registry_option: str | None) -> RegistryTarget:
    """Resolve --registry / env / default, or abort with a clear message."""
    try:
        return resolve_registry(registry_option)
    except InvalidRegistryURL as e:
        click.secho(f"  {e}", fg="red")
        click.echo(f"  Pass a full URL with --registry, for example {LOCAL_HOST_URL}")
        raise SystemExit(1)


def client_for(target: RegistryTarget, token: str | None = None):
    return marketplace_api.MarketplaceClient(target.url, token=token)


def default_token_name() -> str:
    """Label stored with the token on the server, so tokens are identifiable."""
    try:
        host = socket.gethostname().strip()
    except OSError:
        host = ""
    return f"splent-cli@{host}" if host else "splent-cli"


# ── Output helpers ────────────────────────────────────────────────────


def field(label: str, value: str, *, fg: str | None = None) -> None:
    click.echo(f"  {label:<{LABEL_WIDTH}} {click.style(value, fg=fg)}")


def format_scopes(scopes) -> str:
    return ", ".join(scopes) if scopes else "(none reported)"


def format_expiry(expires_at: str | None) -> str:
    """Human reading of an ISO timestamp, with a relative hint when parseable."""
    if not expires_at:
        return "(no expiry reported)"
    raw = str(expires_at).strip()
    parsed = None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed - datetime.now(timezone.utc)
    days = delta.days
    if delta.total_seconds() <= 0:
        return f"{raw} (expired)"
    if days >= 1:
        return f"{raw} (in {days} day{'s' if days != 1 else ''})"
    return f"{raw} (in less than a day)"


def retry_hint(retry_after: str | None) -> str:
    """Turn a ``Retry-After`` value into an instruction, however it is spelled.

    The header is either a number of seconds or an HTTP date. Anything else is
    quoted verbatim rather than dropped, because a wrong-looking value the
    developer can see beats a silent omission.
    """
    raw = (retry_after or "").strip()
    if not raw:
        return "Wait a moment and run the command again."
    if raw.isdigit():
        seconds = int(raw)
        if seconds <= 1:
            return "Wait a second and run the command again."
        if seconds < 120:
            return f"Wait {seconds} seconds and run the command again."
        minutes = round(seconds / 60)
        return f"Wait about {minutes} minutes and run the command again."
    return f"The server asked to retry after {raw}."


def credential_source_label(cred: credentials.Credential) -> str:
    if cred.from_environment:
        return f"environment variable {cred.source_label}"
    return f"file {cred.source_label}"


# ── Error reporting ───────────────────────────────────────────────────


def detail(err: marketplace_api.MarketplaceError) -> None:
    """Print the cause on its own dim line, without repeating the headline."""
    text = err.reason or err.message
    if text:
        click.secho(f"  {text}", fg="bright_black")


def _unreachable_hints(url: str) -> None:
    click.echo("  Check that the marketplace is running and that the URL is right.")
    click.echo(
        f"  Inside splent_cli_container use {LOCAL_CONTAINER_URL}, "
        f"from the host use {LOCAL_HOST_URL}"
    )
    click.echo(f"  Override it with --registry <url> or by setting {REGISTRY_URL_ENV}")


def report_error(err: marketplace_api.MarketplaceError, *, url: str) -> None:
    """Print an actionable explanation of *err*. Never prints the token."""
    code = err.code
    api = marketplace_api

    if code == api.CODE_UNREACHABLE:
        click.secho(f"  Could not reach the marketplace at {url}.", fg="red")
        detail(err)
        _unreachable_hints(url)
        return

    if code == api.CODE_INVALID_CREDENTIALS:
        click.secho("  The marketplace rejected that e-mail and password.", fg="red")
        click.echo("  Check the e-mail for typos and try again.")
        click.echo(f"  If you forgot the password, reset it at {url}")
        return

    if code == api.CODE_ACCOUNT_INACTIVE:
        click.secho("  Your marketplace account is not active yet.", fg="red")
        click.echo(
            "  The marketplace creates accounts inactive until an "
            "administrator activates them,"
        )
        click.echo(
            "  so this is expected right after signing up. Nothing is wrong "
            "with your password."
        )
        click.echo(
            "  Ask an administrator to activate the account, then run "
            "splent login again."
        )
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        return

    if code == api.CODE_TOKEN_EXPIRED:
        click.secho("  Your marketplace token has expired.", fg="red")
        click.echo("  Run splent login to get a new one.")
        return

    if code == api.CODE_TOKEN_REVOKED:
        click.secho("  Your marketplace token was revoked on the server.", fg="red")
        click.echo("  Run splent login to get a new one.")
        return

    if code == api.CODE_UNAUTHENTICATED:
        click.secho("  The marketplace rejected the token.", fg="red")
        click.echo("  Run splent login to authenticate again.")
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        return

    if code == api.CODE_FORBIDDEN:
        click.secho("  Your token is valid but not allowed to do that.", fg="red")
        click.echo(
            "  Ask an administrator for the missing scope, then run "
            "splent login again to pick it up."
        )
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        return

    if code == api.CODE_RATE_LIMITED:
        click.secho("  The marketplace is rate limiting this client.", fg="red")
        click.echo(f"  {retry_hint(err.retry_after)}")
        click.echo(
            "  Nothing is wrong with your password or your token. Marketplaces "
            "throttle repeated"
        )
        click.echo("  sign-in attempts, so this clears on its own.")
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        return

    if code == api.CODE_NOT_FOUND:
        click.secho(f"  The marketplace has nothing at that address ({url}).", fg="red")
        click.echo(
            "  The registry URL may point somewhere that is not a SPLENT "
            "marketplace, or the"
        )
        click.echo(
            "  server may be older than this CLI. Check the URL and the server version."
        )
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        return

    if code == api.CODE_INVALID_REQUEST:
        click.secho("  The marketplace rejected the request.", fg="red")
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        click.echo("  Fix the reported value and run the command again.")
        return

    if code == api.CODE_CONFLICT:
        click.secho("  The marketplace reported a conflict.", fg="red")
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        click.echo("  Reconcile the existing resource and run the command again.")
        return

    if code == api.CODE_PUBLISHING_DISABLED:
        # Reported apart from a server error, which is what its 503 would
        # otherwise be read as. Nothing is broken and retrying changes
        # nothing: publishing is the one path that spends the marketplace's
        # single key, and somebody has deliberately closed it.
        click.secho("  This marketplace is not accepting publications.", fg="red")
        click.echo(
            "  Publishing is switched off there, so retrying will not help. Reading the"
        )
        click.echo("  registry still works. Ask whoever runs it to arm publishing.")
        if err.server_message:
            click.secho(f"  Server said {err.server_message}", fg="bright_black")
        return

    if code == api.CODE_SERVER_ERROR:
        click.secho(
            f"  The marketplace failed to handle the request (HTTP {err.status}).",
            fg="red",
        )
        click.echo("  This is a server-side problem, so retry in a moment.")
        click.echo(f"  If it persists, report it with the time and the registry {url}")
        return

    if code == api.CODE_BAD_RESPONSE:
        click.secho("  The marketplace answered something unexpected.", fg="red")
        click.secho(f"  {err.message}", fg="bright_black")
        click.echo(
            "  Check that the registry URL points at a SPLENT marketplace and that the"
        )
        click.echo("  server and this CLI are on compatible versions.")
        return

    status = f"HTTP {err.status}" if err.status else "no status"
    click.secho(f"  Unexpected marketplace error ({status}).", fg="red")
    if err.message:
        click.secho(f"  {err.message}", fg="bright_black")
    click.echo("  Retry the command, and report it if it happens again.")


def forget_dead_token(url: str) -> bool:
    """Drop a credential the server refused, so the next run says "not logged in"."""
    try:
        removed = credentials.delete(url)
    except credentials.CredentialsError as e:
        click.secho(f"  {e}", fg="yellow")
        return False
    if removed:
        click.secho(
            "  The stored token was removed, so this stops repeating.",
            fg="yellow",
        )
    return removed


def handle_error(
    err: marketplace_api.MarketplaceError,
    *,
    url: str,
    forget: bool = False,
) -> None:
    """Report *err* and, when the credential is dead, drop it from the store."""
    report_error(err, url=url)
    if forget and err.dead_token:
        forget_dead_token(url)
