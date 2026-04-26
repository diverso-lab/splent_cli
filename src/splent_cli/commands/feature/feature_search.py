import click

from splent_cli.services.api_client import SplentAPIError, get_packages


def _contract_description(package: dict) -> str:
    contract = package.get("contract") or {}
    return contract.get("description") or ""


def _updated_at(package: dict) -> str:
    value = package.get("updated_at") or ""
    if "T" in value:
        return value.split("T", 1)[0]
    return value or "-"


def _load_packages() -> list[dict]:
    data = get_packages()
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise SplentAPIError("Unexpected package response.")


@click.command("feature:search", short_help="Search for available features.")
@click.argument("query", required=False)
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Show all packages, not just splent_feature_* ones.",
)
def feature_search(query, show_all):
    """
    List available features.

    \b
    By default filters packages that match the splent_feature_* naming
    convention.
    Optionally filter by QUERY (partial name match).

    Examples:
        splent feature:search
        splent feature:search auth
        splent feature:search --all
    """
    click.echo(click.style("\n  Searching features...\n", fg="cyan"))

    try:
        packages = _load_packages()
    except SplentAPIError as exc:
        click.secho(f"❌ {exc}", fg="red")
        click.echo("   Check SPLENT_API_URL or start the package index.")
        raise SystemExit(1)

    if not show_all:
        packages = [
            p for p in packages if (p.get("name") or "").startswith("splent_feature_")
        ]

    if query:
        packages = [
            p for p in packages if query.lower() in (p.get("name") or "").lower()
        ]

    if not packages:
        msg = "No packages found"
        if query:
            msg += f" matching '{query}'"
        click.secho(f"ℹ️  {msg}.", fg="yellow")
        return

    click.secho(f"  Found {len(packages)} package(s):\n", fg="cyan")

    name_col = max(len(p.get("name") or "-") for p in packages) + 2
    date_col = 12

    for package in sorted(packages, key=lambda p: p.get("name") or ""):
        name = package.get("name") or "-"
        desc = _contract_description(package)
        updated = _updated_at(package)
        click.echo(f"  {name:<{name_col}} {updated:<{date_col}} {desc}")

    click.echo()


cli_command = feature_search
