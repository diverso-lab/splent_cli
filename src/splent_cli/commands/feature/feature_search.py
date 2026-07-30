"""
feature:search — Find features in the marketplace index (or live on GitHub).

With an index available (built via `marketplace:index` or fetched from
SPLENT_INDEX_URL) the search is instant, works offline, and can filter by
what features actually ARE (category, archetype, tags) and by their
contracts (--provides, --requires). Passing --org or --all switches to the
historical live GitHub listing.
"""

import os

import click

from splent_cli.services import context, marketplace, registry


# ── Live GitHub fallback ──────────────────────────────────────────────


def _live_search(query: str | None, org: str, show_all: bool, token: str | None):
    """List repos of a GitHub organisation (the pre-index behavior)."""
    click.echo(click.style(f"\n🔍 Searching features in {org}...\n", fg="cyan"))

    try:
        repos = registry.list_org_repos(org, token)
    except registry.RegistryError as e:
        if e.rate_limited:
            click.secho("❌ GitHub API rate limit exceeded.", fg="red")
        elif e.status == 403:
            click.secho("❌ GitHub API access forbidden (HTTP 403).", fg="red")
        else:
            click.secho(f"❌ {e}.", fg="red")
        if not token:
            click.secho(
                "💡 Set GITHUB_TOKEN to raise your rate limit and access private repos.",
                fg="yellow",
            )
        raise SystemExit(1)

    if repos is None:
        click.secho(f"❌ Organisation '{org}' not found or not accessible.", fg="red")
        raise SystemExit(1)

    if not show_all:
        repos = [r for r in repos if "feature" in r.get("name", "").lower()]
    if query:
        repos = [r for r in repos if query.lower() in r.get("name", "").lower()]

    if not repos:
        msg = f"No features found in {org}"
        if query:
            msg += f" matching '{query}'"
        click.secho(f"ℹ️  {msg}.", fg="yellow")
        return

    click.secho(f"Found {len(repos)} feature(s) in {org}:\n", fg="cyan")

    col = max(len(r["name"]) for r in repos) + 2
    for repo in sorted(repos, key=lambda r: r["name"]):
        name = repo["name"]
        desc = repo.get("description") or ""
        latest = registry.latest_semver_tag(org, name, token)
        version_label = (
            click.style(latest, fg="green")
            if latest
            else click.style("no releases", fg="yellow")
        )
        click.echo(f"  {name:<{col}} {version_label:<20}  {desc}")

    click.echo()
    if not token:
        click.secho(
            "💡 Set GITHUB_TOKEN to avoid rate limits and access private repos.",
            fg="yellow",
        )


# ── Index-backed search ───────────────────────────────────────────────


def _product_feature_shorts() -> set[str]:
    product = context.active_app()
    if not product:
        return set()
    try:
        from splent_cli.utils.feature_utils import load_product_features

        entries = load_product_features(
            os.path.join(str(context.workspace()), product), os.getenv("SPLENT_ENV")
        )
    except (FileNotFoundError, SystemExit):
        return set()
    return {
        e.split("@")[0].split("/")[-1].removeprefix("splent_feature_") for e in entries
    }


def _index_search(index, origin, query, category, archetype, tag, provides, requires):
    results = marketplace.search_features(
        index,
        query,
        category=category,
        archetype=archetype,
        tag=tag,
        provides=provides,
        requires=requires,
    )

    click.echo()
    criteria = [
        f"'{query}'" if query else None,
        f"category={category}" if category else None,
        f"archetype={archetype}" if archetype else None,
        f"tag={tag}" if tag else None,
        f"provides={provides}" if provides else None,
        f"requires={requires}" if requires else None,
    ]
    criteria_label = " ".join(c for c in criteria if c)
    title = "  Marketplace search" + (f" — {criteria_label}" if criteria_label else "")
    click.echo(click.style(title, bold=True))
    click.echo(click.style(f"  {'─' * 72}", fg="bright_black"))

    if not results:
        click.secho("  No features match.", fg="yellow")
        click.echo()
        return

    installed = _product_feature_shorts()

    col_name = max(len(e["short"]) for e in results) + 2
    for e in results:
        version = e.get("version") or e.get("project_version") or "—"
        arch = e.get("archetype") or "?"
        desc = e.get("description") or ""
        if len(desc) > 56:
            desc = desc[:53] + "…"
        mark = click.style("● ", fg="green") if e["short"] in installed else "  "
        click.echo(
            f"  {mark}{e['short']:<{col_name}}"
            + click.style(f"{version:<10}", fg="green")
            + click.style(f"{arch:<9}", fg="cyan")
            + desc
        )

    click.echo()
    click.echo(
        click.style(
            f"  {len(results)} feature(s) · index from {origin} · "
            "details: splent feature:info <name>",
            fg="bright_black",
        )
    )
    click.echo()


# ── Command ───────────────────────────────────────────────────────────


@click.command(
    "marketplace:search",
    short_help="Search features in the marketplace index (or live on GitHub).",
)
@click.argument("query", required=False)
@click.option(
    "--org",
    default=None,
    help="Search this GitHub organisation live instead of the index.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Live mode: show all repos, not just splent_feature_* ones.",
)
@click.option("--category", default=None, help="Filter by contract category.")
@click.option(
    "--archetype",
    default=None,
    type=click.Choice(["full", "light", "service", "config"]),
    help="Filter by feature archetype.",
)
@click.option("--tag", default=None, help="Filter by contract tag.")
@click.option(
    "--provides",
    default=None,
    help="Filter by provided route/service/model/hook/signal (exact name).",
)
@click.option(
    "--requires",
    default=None,
    help="Filter by required feature short name (e.g. auth).",
)
@click.option(
    "--refresh", is_flag=True, help="Re-fetch the index from SPLENT_INDEX_URL."
)
def feature_search(
    query, org, show_all, category, archetype, tag, provides, requires, refresh
):
    """
    Search for available features.

    \b
    Uses the marketplace index when one is available (marketplace:index or
    SPLENT_INDEX_URL); otherwise lists the GitHub organisation live.

    \b
    Examples:
        splent feature:search
        splent feature:search auth
        splent feature:search --archetype service
        splent feature:search --provides MediaService
        splent feature:search --requires auth
        splent feature:search --org my-org        # live GitHub listing
    """
    token = registry.github_token()

    # Explicit --org / --all always means the live GitHub listing.
    if org or show_all:
        _live_search(query, org or "splent-io", show_all, token)
        return

    workspace = str(context.workspace())
    index, origin = marketplace.resolve_index(workspace, refresh=refresh)

    if index is None:
        # No index anywhere — behave like the historical live search.
        _live_search(query, "splent-io", show_all, token)
        click.secho(
            "💡 Build a local index for instant, filterable search: "
            "splent marketplace:index",
            fg="yellow",
        )
        return

    _index_search(index, origin, query, category, archetype, tag, provides, requires)


# Two names, and the reason is a collision that had no good answer.
#
# This command searches the MARKETPLACE for features, so marketplace:search
# is what it should always have been called, next to marketplace:index.
# feature:search stays because it is in everybody's fingers and in scripts,
# and it keeps working in every product that does not install a feature
# actually called "search". Where one is installed, feature:search is that
# feature's own namespace and this command answers to its proper name.
marketplace_search = feature_search
feature_search_alias = click.Command(
    name="feature:search",
    callback=feature_search.callback,
    params=feature_search.params,
    help=feature_search.help,
    short_help="Alias of marketplace:search.",
)

cli_commands = [marketplace_search, feature_search_alias]
