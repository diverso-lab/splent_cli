"""
feature:info — Show a feature's contract card without cloning anything.

Data comes from the marketplace index (built with `marketplace:index` or
fetched from SPLENT_INDEX_URL). When no index is available, falls back to
reading the feature's pyproject.toml straight from GitHub at its latest
released tag — always the real contract, never a hand-maintained copy.
"""

import json
import os
import tomllib

import click

from splent_cli.services import context, marketplace, registry
from splent_cli.utils.feature_utils import (
    get_normalize_feature_name_in_splent_format,
    load_product_features,
)


def _fetch_live_entry(org: str, repo: str, token: str | None) -> dict | None:
    """Build an index-shaped entry for one feature straight from GitHub."""
    version = registry.latest_semver_tag(org, repo, token)
    if not version:
        return None
    text = registry.fetch_file(org, repo, "pyproject.toml", ref=version, token=token)
    if text is None:
        return None
    try:
        return marketplace.feature_entry_from_pyproject(
            text, org=org, repo=repo, source="github", version=version
        )
    except tomllib.TOMLDecodeError:
        return None


def _product_feature_shorts() -> set[str]:
    """Short names of the features in the active product ('' set when detached)."""
    product = context.active_app()
    if not product:
        return set()
    try:
        entries = load_product_features(
            os.path.join(str(context.workspace()), product), os.getenv("SPLENT_ENV")
        )
    except (FileNotFoundError, SystemExit):
        return set()
    return {
        e.split("@")[0].split("/")[-1].removeprefix("splent_feature_") for e in entries
    }


def _print_list(label: str, items: list, color: str | None = None) -> None:
    if not items:
        return
    click.echo(f"  {label:<14}" + click.style(", ".join(items), fg=color))


@click.command(
    "feature:info",
    short_help="Show a feature's contract, dependencies and usage without cloning it.",
)
@click.argument("feature_ref")
@click.option(
    "--refresh", is_flag=True, help="Re-fetch the index from SPLENT_INDEX_URL."
)
@click.option("--json", "as_json", is_flag=True, help="Print the raw entry as JSON.")
def feature_info(feature_ref, refresh, as_json):
    """
    Show everything the marketplace knows about a feature.

    \b
    Examples:
      splent feature:info auth
      splent feature:info splent-io/splent_feature_events
      splent feature:info media --json
    """
    workspace = str(context.workspace())
    token = registry.github_token()

    index, origin = marketplace.resolve_index(workspace, refresh=refresh)

    entry = None
    if index:
        entry = marketplace.find_feature(index, feature_ref)

    if entry is None:
        # Live fallback: one feature, straight from GitHub.
        if "/" in feature_ref:
            org, _, repo = feature_ref.partition("/")
        else:
            org = os.getenv("SPLENT_DEFAULT_ORG", "splent-io")
            repo = feature_ref
        repo = get_normalize_feature_name_in_splent_format(repo.split("@")[0])
        entry = _fetch_live_entry(org, repo, token)
        origin = "github (live)"

    if entry is None:
        click.secho(f"❌ Feature '{feature_ref}' not found.", fg="red")
        click.secho(
            "   Build an index with: splent marketplace:index "
            "(or check the name/org and your network).",
            fg="yellow",
        )
        raise SystemExit(1)

    if as_json:
        click.echo(json.dumps(entry, indent=2, ensure_ascii=False))
        return

    installed = _product_feature_shorts()
    provides = entry.get("provides", {})
    requires = entry.get("requires", {})

    click.echo()
    header = f"  {entry['id']}"
    if entry.get("version"):
        header += click.style(f"  {entry['version']}", fg="green")
    if entry.get("archetype"):
        header += click.style(f"  [{entry['archetype']}]", fg="cyan")
    if entry["short"] in installed:
        header += click.style("  ● installed", fg="green")
    click.echo(click.style(header, bold=True))
    click.echo(click.style(f"  {'─' * 60}", fg="bright_black"))

    if entry.get("description"):
        click.echo(f"  {entry['description']}")
        click.echo()

    if entry.get("category"):
        click.echo(f"  {'category':<14}{entry['category']}")
    _print_list("tags", entry.get("tags", []))
    if entry.get("env"):
        click.echo(f"  {'env':<14}" + click.style(f"{entry['env']} only", fg="yellow"))

    _print_list("models", provides.get("models", []))
    _print_list("services", provides.get("services", []))
    _print_list("routes", provides.get("routes", []))
    _print_list("hooks", provides.get("hooks", []))
    _print_list("signals", provides.get("signals", []))
    _print_list("commands", provides.get("commands", []))
    _print_list("translations", provides.get("translations", []))
    if entry.get("docker"):
        _print_list("docker", entry["docker"].get("services", []), color="cyan")

    req_features = requires.get("features", [])
    if req_features:
        click.echo()
        decorated = []
        for dep in req_features:
            if dep in installed:
                decorated.append(dep + click.style(" ✔", fg="green"))
            else:
                decorated.append(dep + click.style(" ✗ missing", fg="yellow"))
        click.echo(f"  {'requires':<14}" + ", ".join(decorated))
    _print_list("env vars", requires.get("env_vars", []), color="bright_black")
    _print_list("needs signals", requires.get("signals", []), color="bright_black")

    if index:
        closure = marketplace.dependency_closure(index, entry["short"])
        extra = sorted(set(closure) - set(req_features))
        if extra:
            _print_list("transitively", extra, color="bright_black")

    if entry.get("used_by"):
        click.echo()
        _print_list("used by", entry["used_by"], color="cyan")

    if entry.get("refinement"):
        refines = entry["refinement"].get("refines")
        if refines:
            click.echo()
            click.echo(f"  {'refines':<14}" + click.style(refines, fg="magenta"))

    pypi = entry.get("pypi")
    if pypi is not None:
        click.echo()
        if pypi.get("published"):
            label = f"published (latest {pypi.get('latest')})"
            color = "green" if pypi.get("has_current") else "yellow"
            if not pypi.get("has_current"):
                label += " — current tag not on PyPI yet"
            click.echo(f"  {'pypi':<14}" + click.style(label, fg=color))
        else:
            click.echo(
                f"  {'pypi':<14}"
                + click.style("not published (dev-only via GitHub)", fg="yellow")
            )

    gh = entry.get("github") or {}
    if gh.get("url"):
        click.echo(f"  {'repo':<14}{gh['url']}")

    click.echo()
    click.echo(
        click.style(
            f"  source: {origin or entry.get('source', '?')}", fg="bright_black"
        )
    )
    if entry["short"] not in installed and context.active_app():
        click.echo()
        click.echo(
            "  install with: "
            + click.style(
                f"splent feature:install {entry['org']}/{entry['repo']}", bold=True
            )
        )
    click.echo()


cli_command = feature_info
