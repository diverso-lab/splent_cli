import click

from splent_cli.services.api_client import SplentAPIError, get_packages


def _contract_description(package: dict) -> str:
    contract = package.get("contract") or {}
    return contract.get("description") or ""


def _contract_items(package: dict, key: str) -> list[str]:
    contract = package.get("contract") or {}
    values = contract.get(key) or {}

    if isinstance(values, dict):
        items = []
        for name, value in sorted(values.items()):
            if isinstance(value, list):
                if value:
                    items.append(f"{name}: {', '.join(str(item) for item in value)}")
            elif value:
                items.append(f"{name}: {value}")
        return items
    if isinstance(values, list):
        return sorted(str(name) for name in values)
    if isinstance(values, str):
        return [values]
    return []


def _package_matches(package: dict, query: str) -> bool:
    haystack = [
        package.get("name") or "",
        package.get("full_name") or "",
        _contract_description(package),
        " ".join(_contract_items(package, "provides")),
        " ".join(_contract_items(package, "requires")),
    ]
    return query.lower() in " ".join(haystack).lower()


def _format_items(items: list[str]) -> str:
    if not items:
        return "-"
    return ", ".join(items)


def _repo_url(package: dict) -> str | None:
    return package.get("html_url") or None


def _updated_at(package: dict) -> str:
    value = package.get("updated_at") or ""
    if "T" in value:
        return value.split("T", 1)[0]
    return value or "-"


def _load_packages() -> list[dict]:
    data = get_packages()
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise SplentAPIError("The SPLENT API returned an unexpected packages payload.")


@click.command("feature:search", short_help="Search for available SPLENT packages.")
@click.argument("query", required=False)
@click.option(
    "--org",
    default="splent-io",
    show_default=True,
    help="Deprecated. The API decides which GitHub organisation to read.",
)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show all API packages, not just splent_feature_* packages.",
)
def feature_search(query, org, show_all):
    """
    List available packages from the SPLENT API.

    \b
    By default reads SPLENT_API_URL/api/packages and filters by packages that
    match the splent_feature_* naming convention.
    Optionally filter by QUERY (partial name match).

    Examples:
        splent feature:search
        splent feature:search auth
        SPLENT_API_URL=http://127.0.0.1:5000 splent feature:search
    """
    if org != "splent-io":
        click.secho(
            "⚠️  --org is ignored when searching via the SPLENT API.",
            fg="yellow",
        )

    click.echo(click.style("\n🔍 Searching packages in SPLENT API...\n", fg="cyan"))

    try:
        packages = _load_packages()
    except SplentAPIError as exc:
        click.secho(f"❌ {exc}", fg="red")
        click.echo("   Start splent-api or set SPLENT_API_URL to the API base URL.")
        raise SystemExit(1)

    if not show_all:
        packages = [
            p for p in packages if (p.get("name") or "").startswith("splent_feature_")
        ]

    if query:
        packages = [p for p in packages if _package_matches(p, query)]

    if not packages:
        msg = "No packages found"
        if query:
            msg += f" matching '{query}'"
        click.secho(f"ℹ️  {msg}.", fg="yellow")
        return

    click.secho(f"Found {len(packages)} package(s):\n", fg="cyan")

    col = max(len(p.get("name") or "") for p in packages) + 2

    for package in sorted(packages, key=lambda p: p.get("name") or ""):
        name = package.get("name") or "-"
        desc = _contract_description(package)
        updated = _updated_at(package)
        provides = _format_items(_contract_items(package, "provides"))
        requires = _format_items(_contract_items(package, "requires"))

        click.echo(f"  {name:<{col}} updated {updated}  {desc}")
        click.echo(f"  {'':<{col}} provides: {provides}")
        click.echo(f"  {'':<{col}} requires: {requires}")

        url = _repo_url(package)
        if url:
            click.echo(f"  {'':<{col}} {url}")

        click.echo()

    click.echo("Use SPLENT_API_URL to point this command at another splent-api server.")


cli_command = feature_search
