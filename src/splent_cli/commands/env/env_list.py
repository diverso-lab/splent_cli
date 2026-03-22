import os
import click
from pathlib import Path
from splent_cli.services import context


_SENSITIVE = ("TOKEN", "SECRET", "PASSWORD", "KEY", "PWD", "PASS")

_CATEGORIES = [
    ("SPLENT_",   "SPLENT"),
    ("GITHUB_",   "GitHub"),
    ("PYPI_",     "PyPI"),
    ("DB_",       "Database"),
    ("REDIS_",    "Redis"),
    ("MAIL_",     "Mail"),
    ("AWS_",      "AWS"),
    ("CELERY_",   "Celery"),
]


def _mask(key: str, value: str) -> str:
    if any(s in key.upper() for s in _SENSITIVE):
        if len(value) > 10:
            return value[:4] + "****" + value[-2:]
        return "********"
    return value


def _categorise(keys: list[str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {}
    assigned: set[str] = set()
    for prefix, label in _CATEGORIES:
        group = [k for k in keys if k.startswith(prefix)]
        if group:
            buckets[label] = group
            assigned.update(group)
    rest = [k for k in keys if k not in assigned]
    if rest:
        buckets["Other"] = rest
    return buckets


def _read_env_file(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


@click.command("env:list", short_help="List variables in the active .env file.")
@click.argument("filter", required=False, metavar="FILTER")
@click.option("--keys-only", is_flag=True, help="Print only variable names, one per line.")
@click.option("--no-mask", is_flag=True, help="Show sensitive values in plain text.")
@click.option("--unset", is_flag=True, help="Show only variables that are NOT currently set in the process environment.")
def env_list(filter, keys_only, no_mask, unset):
    """
    List variables defined in /workspace/.env.

    \b
    Optionally filter by a prefix or substring:
        splent env:list GITHUB
        splent env:list DB_

    Sensitive keys (TOKEN, SECRET, PASSWORD, KEY…) are masked by default.
    Use --no-mask to reveal plain values.
    """
    env_file = context.workspace() / ".env"

    if not env_file.exists():
        click.secho(f"❌ No .env file found at {env_file}.", fg="red")
        raise SystemExit(1)

    data = _read_env_file(env_file)

    if not data:
        click.secho("ℹ️  .env file is empty.", fg="yellow")
        return

    # Apply filter
    if filter:
        data = {k: v for k, v in data.items() if filter.upper() in k.upper()}
        if not data:
            click.secho(f"ℹ️  No variables matching '{filter}'.", fg="yellow")
            return

    # Apply --unset filter
    if unset:
        data = {k: v for k, v in data.items() if not os.environ.get(k)}
        if not data:
            click.secho("✅ All variables are set in the current environment.", fg="green")
            return

    # --keys-only: simple list for scripting
    if keys_only:
        for k in sorted(data):
            click.echo(k)
        return

    # Grouped output
    buckets = _categorise(list(data.keys()))
    total = len(data)

    click.echo()
    for label, keys in buckets.items():
        click.secho(f"  {label}", fg="cyan", bold=True)
        col = max(len(k) for k in keys) + 2
        for k in sorted(keys):
            v = data[k]
            display = v if no_mask else _mask(k, v)
            in_env = os.environ.get(k)
            if in_env is None:
                indicator = click.style("·", fg="yellow")  # defined in file, not in env
            elif in_env == v:
                indicator = click.style("✔", fg="green")   # loaded and matches
            else:
                indicator = click.style("≠", fg="yellow")  # loaded but differs
            click.echo(f"    {indicator} {k:<{col}} {display}")
        click.echo()

    note = f"  {total} variable(s) in {env_file}"
    if not no_mask:
        note += "  (sensitive values masked)"
    click.secho(note, fg="bright_black")
    click.echo()


cli_command = env_list
