import logging
import click
import os
import subprocess
import tomllib

from splent_cli.utils.feature_utils import get_features_from_pyproject,get_normalize_feature_name_in_splent_format


logger = logging.getLogger(__name__)


@click.command("webpack:compile", help="Compile webpack for one or all features.")
@click.argument("feature_name", required=False)
@click.option("--watch", is_flag=True, help="Enable watch mode for development.")
def webpack_compile(feature_name, watch):
    production = os.getenv("FLASK_ENV", "develop") == "production"

    features = (
        [get_normalize_feature_name_in_splent_format(feature_name)]
        if feature_name
        else get_features_from_pyproject()
    )

    for feature in features:
        compile_feature(feature, watch, production)


def compile_feature(feature, watch, production):
    """
    Compiles a feature's webpack assets located under:
    /workspace/<product>/features/<feature>@<version>/src/<feature_base>/assets/js/webpack.config.js
    """
    product = os.getenv("SPLENT_APP")
    base_feature_name = feature.split("@")[0]

    webpack_file = os.path.join(
        "/workspace",
        product,
        "features",
        feature,
        "src",
        base_feature_name,
        "assets",
        "js",
        "webpack.config.js",
    )

    if not os.path.exists(webpack_file):
        click.echo(click.style(f"⚠ No webpack.config.js found in {feature}, skipping...", fg="yellow"))
        return

    click.echo(click.style(f"🚀 Compiling {feature}...", fg="cyan"))

    mode = "production" if production else "development"
    extra_flags = "--devtool source-map --no-cache" if not production else ""
    watch_flag = "--watch" if watch and not production else ""

    webpack_command = f"npx webpack --config '{webpack_file}' --mode {mode} {watch_flag} {extra_flags} --color"

    try:
        if watch:
            subprocess.Popen(webpack_command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            click.echo(click.style(f"👀 Watching {feature} in {mode} mode...", fg="blue"))
        else:
            subprocess.run(webpack_command, shell=True, check=True)
            click.echo(click.style(f"✅ Successfully compiled {feature} in {mode} mode!", fg="green"))
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"❌ Error compiling {feature}: {e}", fg="red"))
