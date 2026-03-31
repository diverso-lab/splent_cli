import os

import click

from splent_cli.services import context


# Keys that Flask sets internally and are not interesting to show by default
_FLASK_INTERNAL_KEYS = frozenset(
    {
        "APPLICATION_ROOT",
        "DEBUG",
        "ENV",
        "EXPLAIN_TEMPLATE_LOADING",
        "JSONIFY_MIMETYPE",
        "JSONIFY_PRETTYPRINT_REGULAR",
        "MAX_CONTENT_LENGTH",
        "MAX_COOKIE_SIZE",
        "PERMANENT_SESSION_LIFETIME",
        "PREFERRED_URL_SCHEME",
        "PROPAGATE_EXCEPTIONS",
        "SECRET_KEY",
        "SEND_FILE_MAX_AGE_DEFAULT",
        "SERVER_NAME",
        "SESSION_COOKIE_DOMAIN",
        "SESSION_COOKIE_HTTPONLY",
        "SESSION_COOKIE_NAME",
        "SESSION_COOKIE_PATH",
        "SESSION_COOKIE_SAMESITE",
        "SESSION_COOKIE_SECURE",
        "SESSION_REFRESH_EACH_REQUEST",
        "TEMPLATES_AUTO_RELOAD",
        "TESTING",
        "TRAP_BAD_REQUEST_ERRORS",
        "TRAP_HTTP_EXCEPTIONS",
        "USE_X_SENDFILE",
    }
)


def _short_value(value, max_len: int = 50) -> str:
    """Return a display-friendly representation of a config value."""
    text = repr(value)
    if len(text) > max_len:
        return text[: max_len - 1] + "\u2026"
    return text


def _source_label(info: dict) -> str:
    """Human-readable label for who set this key."""
    source = info.get("source", "unknown")
    # Prettify feature import names: "splent_io.splent_feature_redis" -> "feature (splent_feature_redis)"
    if "." in source:
        parts = source.rsplit(".", 1)
        return f"feature ({parts[1]})"
    return source


def _source_color(label: str) -> str:
    if label.startswith("product"):
        return "cyan"
    if label.startswith("feature"):
        return "yellow"
    if label.startswith("framework"):
        return "bright_black"
    return "white"


@click.command(
    "product:config",
    short_help="Show the resolved configuration with origin tracing.",
)
@click.option(
    "--all", "show_all", is_flag=True, help="Include Flask internal defaults."
)
@click.option(
    "--feature", "filter_feature", default=None, help="Filter by feature name."
)
@click.option(
    "--key", "filter_key", default=None, help="Filter by config key (substring match)."
)
@context.requires_product
def product_config(show_all, filter_feature, filter_key):
    """Show the final resolved configuration of the active product.

    For each config key, displays its current value and which layer set it
    (product config, feature inject_config, or service init_app).

    Warns about keys that were overwritten by a later layer.
    """
    product = context.require_app()

    # Boot the app to trigger all config loading with tracing
    os.environ.setdefault("SPLENT_ENV", "dev")
    from splent_cli.utils.dynamic_imports import get_app

    app = get_app()

    trace: dict = app.extensions.get("splent_config_trace", {})

    # Separate overwritten keys
    overwrites = {k: v for k, v in trace.items() if v.get("action") == "overwritten"}

    # Build display rows: (key, value, source_label)
    rows = []
    for key in sorted(app.config.keys()):
        if not key.isupper():
            continue
        if not show_all and key in _FLASK_INTERNAL_KEYS:
            continue

        info = trace.get(key)
        if info:
            label = _source_label(info)
        else:
            label = "flask default"

        if filter_feature and filter_feature not in label:
            continue
        if filter_key and filter_key.upper() not in key:
            continue

        rows.append((key, _short_value(app.config[key]), label))

    if not rows:
        click.secho("No config keys matched the filters.", fg="yellow")
        return

    # Calculate column widths
    col_key = max(len(r[0]) for r in rows)
    col_val = max(len(r[1]) for r in rows)
    col_src = max(len(r[2]) for r in rows)
    col_key = max(col_key, 3)
    col_val = max(col_val, 5)
    col_src = max(col_src, 6)

    # Header
    env = os.getenv("SPLENT_ENV", "dev")
    feature_sources = set()
    for v in trace.values():
        src = v.get("source", "")
        if "." in src:
            feature_sources.add(src.rsplit(".", 1)[1])

    click.echo()
    click.secho(
        f"  {product}  [{env}]  {len(feature_sources)} feature(s) injecting config",
        bold=True,
    )
    click.echo()

    header = f"  {'KEY':<{col_key}}  {'VALUE':<{col_val}}  {'SOURCE':<{col_src}}"
    click.secho(header, fg="cyan")
    click.echo("  " + "\u2500" * (col_key + col_val + col_src + 4))

    for key, val, source in rows:
        color = _source_color(source)
        source_styled = click.style(f"{source:<{col_src}}", fg=color)
        click.echo(f"  {key:<{col_key}}  {val:<{col_val}}  {source_styled}")

    # Overwrite warnings
    if overwrites:
        click.echo()
        click.secho("  \u26a0  Overwritten keys:", fg="red", bold=True)
        for key, info in sorted(overwrites.items()):
            prev_src = info.get("prev_source", "?")
            prev_val = _short_value(info.get("prev_value", "?"), 30)
            new_src = _source_label(info)
            new_val = _short_value(info.get("value", "?"), 30)
            click.echo(
                f"     {key}: {prev_val} ({prev_src}) \u2192 {new_val} ({new_src})"
            )

    click.echo()


cli_command = product_config
