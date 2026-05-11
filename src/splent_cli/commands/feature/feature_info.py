import shutil
import textwrap

import click

from splent_cli.services.api_client import SplentAPIError, get_package_by_name


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


def _terminal_width() -> int:
    return min(shutil.get_terminal_size((100, 20)).columns, 120)


def _echo_wrapped(label: str, value: str, width: int) -> None:
    prefix = f"  {label:<12}"
    wrapped = textwrap.wrap(
        value or "-",
        width=max(width - len(prefix), 30),
        subsequent_indent=" " * len(prefix),
    )
    if not wrapped:
        click.echo(prefix + "-")
        return

    click.echo(prefix + wrapped[0])
    for line in wrapped[1:]:
        click.echo(line)


def _echo_contract_section(label: str, items: list[str], width: int) -> None:
    click.echo(f"  {label}")
    if not items:
        click.echo("    -")
        return

    for item in items:
        wrapped = textwrap.wrap(
            item,
            width=max(width - 6, 30),
            initial_indent="    - ",
            subsequent_indent="      ",
        )
        for line in wrapped:
            click.echo(line)


def _repo_url(package: dict) -> str | None:
    return package.get("repo_url") or None


def _updated_at(package: dict) -> str:
    metadata = package.get("metadata") or {}
    value = metadata.get("updated_at") or package.get("updated_at") or ""
    if "T" in value:
        return value.split("T", 1)[0]
    return value or "-"


def _feature_api_name(feature_name: str) -> str:
    if "/" in feature_name:
        owner, name = feature_name.split("/", 1)
        if name.startswith("splent_feature_"):
            return f"{owner}/{name}"
        return f"{owner}/splent_feature_{name}"

    if feature_name.startswith("splent_feature_"):
        return feature_name
    return f"splent_feature_{feature_name}"


def _feature_api_candidates(feature_name: str) -> list[str]:
    if "/" in feature_name:
        candidates = [feature_name]
        normalized = _feature_api_name(feature_name)
        if normalized not in candidates:
            candidates.append(normalized)
        return candidates

    candidates = [_feature_api_name(feature_name)]
    if feature_name not in candidates:
        candidates.append(feature_name)
    return candidates


@click.command("feature:info", short_help="Show marketplace feature details.")
@click.argument("feature_name", required=True)
def feature_info(feature_name):
    """
    Show marketplace information for one feature.

    \b
    Examples:
        splent feature:info auth
        splent feature:info splent_feature_auth
    """
    candidates = _feature_api_candidates(feature_name)
    api_name = candidates[0]
    click.echo(click.style(f"\n  Loading feature {api_name}...\n", fg="cyan"))

    package = None
    try:
        for candidate in candidates:
            try:
                package = get_package_by_name(candidate)
            except SplentAPIError as exc:
                if (
                    ("HTTP 404" in str(exc) or "HTTP 500" in str(exc))
                    and candidate != candidates[-1]
                ):
                    continue
                raise
            if isinstance(package, dict):
                api_name = candidate
                break
    except SplentAPIError as exc:
        click.secho(f"❌ {exc}", fg="red")
        click.echo("   Check SPLENT_API_URL or start the package index.")
        raise SystemExit(1)

    if not isinstance(package, dict):
        click.secho("❌ Invalid package response from API.", fg="red")
        raise SystemExit(1)

    width = _terminal_width()
    name = package.get("name") or api_name
    desc = _contract_description(package)
    updated = _updated_at(package)
    provides = _contract_items(package, "provides")
    requires = _contract_items(package, "requires")

    click.secho(f"  {name}", bold=True)
    _echo_wrapped("full name", package.get("full_name") or "-", width)
    _echo_wrapped("updated", updated, width)
    _echo_wrapped("summary", desc, width)
    _echo_contract_section("provides", provides, width)
    _echo_contract_section("requires", requires, width)

    url = _repo_url(package)
    if url:
        _echo_wrapped("repo", url, width)

    click.echo()


cli_command = feature_info
