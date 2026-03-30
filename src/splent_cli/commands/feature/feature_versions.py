import json
import os
import urllib.error
import urllib.request

import click

from splent_cli.services import compose
from splent_cli.utils.feature_utils import load_product_features


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _github_headers(token: str | None) -> dict:
    h = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "splent-cli",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        h["Authorization"] = f"token {token}"
    return h


def _get_json(url: str, headers: dict) -> list | dict | None:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except urllib.error.URLError as e:
        click.secho(f"❌ Network error: {e.reason}", fg="red")
        raise SystemExit(1)


# ── Source fetchers ───────────────────────────────────────────────────────────


def _github_versions(org: str, repo: str, token: str | None) -> list[str]:
    """Return all tag names from the GitHub repo, newest first."""
    headers = _github_headers(token)
    tags = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{org}/{repo}/tags?per_page=100&page={page}"
        batch = _get_json(url, headers)
        if not batch:
            break
        tags.extend(t["name"] for t in batch)
        if len(batch) < 100:
            break
        page += 1
    return tags


def _pypi_versions(package: str) -> list[str]:
    """Return all PyPI release versions, newest first."""
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "splent-cli"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        versions = list(data.get("releases", {}).keys())

        def _latest_upload(v):
            files = data["releases"][v]
            if not files:
                return ""
            return max(f.get("upload_time", "") for f in files)

        return sorted(versions, key=_latest_upload, reverse=True)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    except urllib.error.URLError:
        return []


# ── Version helpers ───────────────────────────────────────────────────────────


def _strip_v(v: str) -> str:
    return v.lstrip("v")


def _versions_behind(declared: str, gh_versions: list[str]) -> int:
    """Return how many positions ahead of declared the latest tag is (0 = up to date)."""
    norm_declared = _strip_v(declared)
    norm_list = [_strip_v(v) for v in gh_versions]
    if norm_declared not in norm_list:
        return -1
    return norm_list.index(norm_declared)


def _status_label(declared: str | None, gh_versions: list[str]) -> tuple[str, str]:
    """Return (label, color) for the status column."""
    if declared is None:
        return "(editable)", "bright_black"
    if not gh_versions:
        return "unknown", "bright_black"
    behind = _versions_behind(declared, gh_versions)
    if behind == 0:
        return "✔ up to date", "green"
    if behind > 0:
        return f"⚠  {behind} behind", "yellow"
    return "? not in tags", "bright_black"


# ── Pyproject helpers ─────────────────────────────────────────────────────────


def _load_active_product_features() -> list[str]:
    """Return raw feature entries from the active product's pyproject.toml."""
    try:
        from splent_cli.services import context

        workspace = str(context.workspace())
        product = context.require_app()
    except SystemExit:
        return []
    try:
        return load_product_features(os.path.join(workspace, product))
    except FileNotFoundError:
        return []


def _declared_version(features: list[str], feature_name: str) -> str | None:
    """Return the declared version for a feature, or None if editable."""
    for entry in features:
        bare = entry.split("@")[0].split("/")[-1]
        if bare == feature_name:
            if "@" in entry:
                return entry.split("@", 1)[1]
            return None
    return None


# ── Diff helper ───────────────────────────────────────────────────────────────


def _pypi_version_exists(package: str, version: str) -> bool:
    """Check if a specific version exists on PyPI via the per-version endpoint.

    The main /json endpoint is CDN-cached and may lag after a fresh release.
    The per-version endpoint is always up to date.
    """
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "splent-cli"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except urllib.error.URLError:
        return False


def _semver_sort_key(v: str) -> tuple:
    clean = _strip_v(v)
    try:
        return tuple(int(x) for x in clean.split(".")[:3])
    except ValueError:
        return (0, 0, 0)


def _print_diff_table(
    gh_versions: list[str], pypi_versions: list[str], package: str
) -> None:
    """Unified version sync table with arrows and colours."""
    gh_norm = {_strip_v(v): v for v in gh_versions}
    pypi_norm = {_strip_v(v): v for v in pypi_versions}

    all_norm = sorted(
        gh_norm.keys() | pypi_norm.keys(),
        key=_semver_sort_key,
        reverse=True,
    )

    COL_VER = 10
    COL_SRC = 16

    click.echo()
    click.echo(
        click.style(f"  {'Version':<{COL_VER}}", bold=True)
        + click.style(f"  {'GitHub':<{COL_SRC}}", bold=True, fg="cyan")
        + click.style("     ")
        + click.style(f"{'PyPI':<{COL_SRC}}", bold=True, fg="magenta")
    )
    click.echo(
        click.style(f"  {'─' * (COL_VER + COL_SRC * 2 + 10)}", fg="bright_black")
    )

    all_in_sync = True
    for v in all_norm:
        in_gh = v in gh_norm
        in_pypi = v in pypi_norm

        # For versions only on GitHub, verify against per-version endpoint
        # to avoid false positives from CDN lag after a fresh release.
        if in_gh and not in_pypi:
            in_pypi_live = _pypi_version_exists(package, v)
            cdn_lag = in_pypi_live
        else:
            in_pypi_live = in_pypi
            cdn_lag = False

        display_ver = click.style(
            f"  {gh_norm.get(v, pypi_norm.get(v, v)):<{COL_VER}}",
            fg="bright_white",
        )

        if in_gh and in_pypi_live:
            gh_col = click.style(f"  {'✔  tagged':<{COL_SRC}}", fg="green")
            arrow = click.style("  ↔  ", fg="green", bold=True)
            pypi_label = "✔  published"
            if cdn_lag:
                pypi_label += " *"
            pypi_col = click.style(f"{pypi_label:<{COL_SRC}}", fg="green")
        elif in_gh:
            all_in_sync = False
            gh_col = click.style(f"  {'✔  tagged':<{COL_SRC}}", fg="green")
            arrow = click.style("  →  ", fg="yellow", bold=True)
            pypi_col = click.style(f"{'✗  not published':<{COL_SRC}}", fg="red")
        else:
            all_in_sync = False
            gh_col = click.style(f"  {'✗  no tag':<{COL_SRC}}", fg="red")
            arrow = click.style("  ←  ", fg="yellow", bold=True)
            pypi_col = click.style(f"{'✔  published':<{COL_SRC}}", fg="magenta")

        click.echo(display_ver + gh_col + arrow + pypi_col)

    click.echo()
    if all_in_sync:
        click.secho("  ✔  GitHub and PyPI are in sync.", fg="green", bold=True)
    else:
        click.secho(
            "  ↔  in sync   →  only on GitHub   ←  only on PyPI",
            fg="bright_black",
        )


# ── Command ───────────────────────────────────────────────────────────────────


@click.command(
    "feature:versions",
    short_help="List all available versions of a feature on GitHub and PyPI.",
)
@click.argument("feature_identifier", required=False, default=None)
@click.option(
    "--github",
    "only_github",
    is_flag=True,
    default=False,
    help="Show only GitHub tags.",
)
@click.option(
    "--pypi", "only_pypi", is_flag=True, default=False, help="Show only PyPI releases."
)
@click.option(
    "--diff",
    "show_diff",
    is_flag=True,
    default=False,
    help="Show versions missing from one source or the other.",
)
@click.option(
    "--latest",
    "show_latest",
    is_flag=True,
    default=False,
    help="Show only the latest available version.",
)
@click.option(
    "--status",
    "show_status",
    is_flag=True,
    default=False,
    help="Compare declared version in pyproject against the latest on GitHub.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    default=False,
    help="Check all features declared in the active product.",
)
def feature_versions(
    feature_identifier,
    only_github,
    only_pypi,
    show_diff,
    show_latest,
    show_status,
    show_all,
):
    """
    List all released versions of a feature from GitHub tags and PyPI.

    \b
    Examples:
        splent feature:versions splent_feature_auth
        splent feature:versions splent_feature_auth --latest
        splent feature:versions splent_feature_auth --status
        splent feature:versions splent_feature_auth --diff
        splent feature:versions splent_feature_auth --github
        splent feature:versions splent_feature_auth --pypi
        splent feature:versions --all
        splent feature:versions --all --status
    """
    token = os.getenv("GITHUB_TOKEN")

    if show_all:
        if feature_identifier:
            raise click.UsageError("Cannot pass a feature name with --all.")
        _cmd_all(token, show_status)
        return

    if not feature_identifier:
        raise click.UsageError(
            "Missing argument '<feature_name>'. Use --all to check all features."
        )

    _, namespace_github, _, feature_name = compose.parse_feature_identifier(
        feature_identifier
    )
    feature_name = feature_name.split("@")[0]

    show_github = not only_pypi
    show_pypi = not only_github

    click.echo()
    click.echo(
        click.style(f"  Versions for {namespace_github}/{feature_name}", bold=True)
    )
    click.echo(click.style(f"  {'─' * 50}", fg="bright_black"))

    gh_versions = (
        _github_versions(namespace_github, feature_name, token)
        if (show_github or show_diff or show_status)
        else []
    )
    pypi_versions = _pypi_versions(feature_name) if (show_pypi or show_diff) else []

    # ── Latest ────────────────────────────────────────────────────────────────
    if show_latest:
        latest_gh = gh_versions[0] if gh_versions else "(none)"
        latest_pypi = pypi_versions[0] if pypi_versions else "(none)"
        click.echo()
        if show_github or (not only_github and not only_pypi):
            click.echo(f"  {'GitHub:':10} {click.style(latest_gh, fg='cyan')}")
        if show_pypi or (not only_github and not only_pypi):
            click.echo(f"  {'PyPI:':10} {click.style(latest_pypi, fg='cyan')}")
        click.echo()
        return

    # ── Status ────────────────────────────────────────────────────────────────
    if show_status:
        features = _load_active_product_features()
        declared = _declared_version(features, feature_name)
        latest_gh = gh_versions[0] if gh_versions else None
        label, color = _status_label(declared, gh_versions)
        click.echo()
        click.echo(
            f"  {'Declared:':12} {click.style(declared or '(editable)', fg='bright_white')}"
        )
        click.echo(
            f"  {'Latest (GH):':12} {click.style(latest_gh or '(none)', fg='cyan')}"
        )
        click.echo(f"  {'Status:':12} {click.style(label, fg=color)}")
        click.echo()
        if not token:
            click.secho("  💡 Set GITHUB_TOKEN to avoid rate limits.", fg="yellow")
            click.echo()
        return

    # ── Diff: unified table replaces the two separate lists ───────────────────
    if show_diff:
        _print_diff_table(gh_versions, pypi_versions, feature_name)
        if not token:
            click.secho("  💡 Set GITHUB_TOKEN to avoid rate limits.", fg="yellow")
            click.echo()
        return

    # ── GitHub ────────────────────────────────────────────────────────────────
    if show_github:
        click.echo(click.style("\n  GitHub tags", fg="cyan"))
        if gh_versions:
            for v in gh_versions:
                click.echo(f"    {v}")
        else:
            click.secho("    (no tags found)", fg="bright_black")

    # ── PyPI ──────────────────────────────────────────────────────────────────
    if show_pypi:
        click.echo(click.style("\n  PyPI releases", fg="cyan"))
        if pypi_versions:
            for v in pypi_versions:
                click.echo(f"    {v}")
        else:
            click.secho("    (not published on PyPI)", fg="bright_black")

    click.echo()
    if show_github and not token:
        click.secho("  💡 Set GITHUB_TOKEN to avoid rate limits.", fg="yellow")
        click.echo()


# ── --all table ───────────────────────────────────────────────────────────────


def _cmd_all(token: str | None, show_status: bool) -> None:
    features = _load_active_product_features()
    if not features:
        click.secho(
            "❌ No features found. Make sure a product is selected and pyproject.toml exists.",
            fg="red",
        )
        raise SystemExit(1)

    COL_NAME = 38
    COL_DECLARED = 14
    COL_LATEST = 14
    COL_STATUS = 20

    click.echo()
    header = (
        f"  {'Feature':<{COL_NAME}}"
        f"{'Declared':<{COL_DECLARED}}"
        f"{'Latest (GH)':<{COL_LATEST}}"
        f"{'Status':<{COL_STATUS}}"
    )
    click.echo(click.style(header, bold=True))
    click.echo(
        click.style(
            f"  {'─' * (COL_NAME + COL_DECLARED + COL_LATEST + COL_STATUS)}",
            fg="bright_black",
        )
    )

    for entry in features:
        bare = entry.split("@")[0].split("/")[-1]
        _, ns_gh, _, _ = compose.parse_feature_identifier(entry.split("@")[0])
        declared = _declared_version(features, bare)

        gh_versions = _github_versions(ns_gh, bare, token)
        latest_gh = gh_versions[0] if gh_versions else None
        label, color = _status_label(declared, gh_versions)

        col_name = f"{bare:<{COL_NAME}}"
        col_declared = f"{(declared or '(editable)'):<{COL_DECLARED}}"
        col_latest = f"{(latest_gh or '(none)'):<{COL_LATEST}}"

        click.echo(
            f"  {col_name}{col_declared}{col_latest}" + click.style(label, fg=color)
        )

    click.echo()
    if not token:
        click.secho("  💡 Set GITHUB_TOKEN to avoid rate limits.", fg="yellow")
        click.echo()


cli_command = feature_versions
