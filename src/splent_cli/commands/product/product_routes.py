import os

import click

from splent_cli.services import context


def _source_label(blueprint_name: str, trace: dict) -> str:
    """Map a blueprint name to its owning feature via the trace."""
    import_name = trace.get(blueprint_name)
    if import_name:
        # "splent_io.splent_feature_auth" -> "splent_feature_auth"
        return import_name.rsplit(".", 1)[-1]
    if blueprint_name in ("static", ""):
        return "(flask)"
    return blueprint_name


def _source_color(label: str) -> str:
    if label.startswith("splent_feature_"):
        return "yellow"
    if label == "(flask)":
        return "bright_black"
    return "white"


@click.command(
    "product:routes",
    short_help="Show all routes with feature attribution.",
)
@click.option(
    "--feature", "filter_feature", default=None, help="Filter by feature name."
)
@click.option("--group", is_flag=True, help="Group routes by feature.")
@context.requires_product
def product_routes(filter_feature, group):
    """Show all HTTP routes registered by the active product.

    Each route is attributed to the feature that registered it.
    """
    product = context.require_app()

    os.environ.setdefault("SPLENT_ENV", "dev")
    from splent_cli.utils.dynamic_imports import get_app

    app = get_app()

    trace: dict = app.extensions.get("splent_blueprint_trace", {})

    # Build route entries: (path, method, blueprint, feature_label)
    entries = []
    for rule in app.url_map.iter_rules():
        methods = sorted(rule.methods.difference({"HEAD", "OPTIONS"}))
        bp_name = rule.endpoint.split(".")[0] if "." in rule.endpoint else ""
        feature = _source_label(bp_name, trace)

        for method in methods:
            entries.append((rule.rule, method, feature, rule.endpoint))

    entries.sort(key=lambda e: (e[0], e[1]))

    # Normalize filter
    if filter_feature:
        target = filter_feature
        if not target.startswith("splent_feature_"):
            target = f"splent_feature_{target}"
        entries = [e for e in entries if e[2] == target]

    if not entries:
        click.secho("No routes matched.", fg="yellow")
        return

    env = os.getenv("SPLENT_ENV", "dev")

    if group:
        # Group by feature
        grouped: dict[str, list[tuple]] = {}
        for path, method, feature, endpoint in entries:
            grouped.setdefault(feature, []).append((path, method, endpoint))

        click.echo()
        click.secho(f"  {product}  [{env}]", bold=True)
        click.echo()

        for feature in sorted(grouped.keys()):
            routes = grouped[feature]
            color = _source_color(feature)
            click.secho(f"  {feature}  ({len(routes)} routes)", fg=color, bold=True)
            for path, method, endpoint in routes:
                method_styled = click.style(f"{method:<6}", fg="magenta")
                click.echo(f"    {method_styled} {path}")
            click.echo()
        return

    # Flat table
    col_route = max(len(e[0]) for e in entries)
    col_method = 6
    col_feature = max(len(e[2]) for e in entries)
    col_route = max(col_route, 5)
    col_feature = max(col_feature, 7)

    feature_set = {e[2] for e in entries if e[2].startswith("splent_feature_")}

    click.echo()
    click.secho(
        f"  {product}  [{env}]  {len(feature_set)} feature(s)  {len(entries)} route(s)",
        bold=True,
    )
    click.echo()

    header = f"  {'ROUTE':<{col_route}}  {'METHOD':<{col_method}}  {'FEATURE':<{col_feature}}  ENDPOINT"
    click.secho(header, fg="cyan")
    click.echo("  " + "\u2500" * (col_route + col_method + col_feature + 30))

    for path, method, feature, endpoint in entries:
        color = _source_color(feature)
        feature_styled = click.style(f"{feature:<{col_feature}}", fg=color)
        method_styled = click.style(f"{method:<{col_method}}", fg="magenta")
        click.echo(
            f"  {path:<{col_route}}  {method_styled}  {feature_styled}  {endpoint}"
        )

    click.echo()


cli_command = product_routes
