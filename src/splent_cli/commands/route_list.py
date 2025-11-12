import os
import click
import tomllib
from flask import current_app
from collections import defaultdict

from splent_cli.utils.decorators import requires_app
from splent_cli.utils.path_utils import PathUtils


def get_feature_names():
    pyproject_path = os.path.join(PathUtils.get_app_base_dir(), "pyproject.toml")

    if not os.path.exists(pyproject_path):
        return []

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        features = data["project"]["optional-dependencies"].get("features", [])
        return [
            f.split("_")[-1] for f in features
        ]  # e.g. "splent_feature_auth" → "auth"
    except Exception:
        return []


@requires_app
@click.command(
    "route:list", help="Lists all routes registered by the Flask application."
)
@click.argument("feature_name", required=False)
@click.option("--group", is_flag=True, help="Group routes by feature.")
def route_list(feature_name, group):
    features = get_feature_names()

    if feature_name and feature_name not in features:
        click.echo(
            click.style(f"❌ Feature '{feature_name}' is not enabled.", fg="red")
        )
        return

    rules = sorted(current_app.url_map.iter_rules(), key=lambda rule: rule.endpoint)

    if feature_name:
        click.echo(click.style(f"📦 Routes for feature '{feature_name}':", fg="green"))
        filtered = [
            rule for rule in rules if rule.endpoint.startswith(f"{feature_name}.")
        ]
        print_route_table(filtered)

    elif group:
        click.echo(click.style("📦 Grouped routes by feature:", fg="green"))
        grouped_rules = defaultdict(list)
        for rule in rules:
            feature = rule.endpoint.split(".")[0]
            grouped_rules[feature].append(rule)

        for feature, rules in sorted(grouped_rules.items()):
            click.echo(click.style(f"\nFeature: {feature}", fg="yellow"))
            print_route_table(rules)
    else:
        click.echo(click.style("📦 All registered routes:", fg="green"))
        print_route_table(rules)


def print_route_table(rules):
    if not rules:
        click.echo(click.style("⚠️ No routes found.", fg="yellow"))
        return

    # Calcula longitudes máximas razonables (limitadas)
    max_endpoint = min(max((len(r.endpoint) for r in rules), default=8), 25)
    max_methods = min(max((len(", ".join(r.methods)) for r in rules), default=7), 20)

    header = f"{'Endpoint'.ljust(max_endpoint)}  {'Methods'.ljust(max_methods)}  Route"
    click.echo(click.style(header, bold=True))
    click.echo("-" * (max_endpoint + max_methods + 2 + 60))

    for rule in rules:
        endpoint = rule.endpoint.ljust(max_endpoint)
        methods = ", ".join(sorted(rule.methods.difference({"HEAD", "OPTIONS"}))).ljust(
            max_methods
        )
        route = rule.rule

        click.echo(
            f"{click.style(endpoint, fg='cyan')}  "
            f"{click.style(methods, fg='magenta')}  "
            f"{route}"
        )


cli_command = route_list
