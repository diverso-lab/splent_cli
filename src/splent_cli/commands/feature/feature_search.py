import urllib.request
import urllib.error
import json
import os
import click


def _github_request(url: str, token: str | None) -> dict | list | None:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "splent-cli",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"token {token}"
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


def _latest_tag(org: str, repo: str, token: str | None) -> str | None:
    data = _github_request(
        f"https://api.github.com/repos/{org}/{repo}/releases/latest", token
    )
    if data and data.get("tag_name"):
        return data["tag_name"]
    # fall back to tags
    tags = _github_request(f"https://api.github.com/repos/{org}/{repo}/tags", token)
    if tags and isinstance(tags, list) and tags:
        return tags[0].get("name")
    return None


@click.command("feature:search", short_help="Search for available features on GitHub.")
@click.argument("query", required=False)
@click.option(
    "--org",
    default="splent-io",
    show_default=True,
    help="GitHub organisation to search in.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show all repos, not just splent_feature_* ones.",
)
def feature_search(query, org, show_all):
    """
    List available features from a GitHub organisation.

    \b
    By default searches the splent-io org and filters by repos that match
    the splent_feature_* naming convention.
    Optionally filter by QUERY (partial name match).

    Examples:
        splent feature:search
        splent feature:search auth
        splent feature:search --org my-org
    """
    token = os.getenv("GITHUB_TOKEN")

    click.echo(click.style(f"\n🔍 Searching features in {org}...\n", fg="cyan"))

    # Paginate through all repos
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page=100&page={page}&type=public"
        batch = _github_request(url, token)
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    if repos is None:
        click.secho(f"❌ Organisation '{org}' not found or not accessible.", fg="red")
        raise SystemExit(1)

    # Filter
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
        latest = _latest_tag(org, name, token)
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


cli_command = feature_search
