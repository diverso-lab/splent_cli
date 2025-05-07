import logging
import click
import os
import subprocess

from splent_cli.utils.path_utils import PathUtils

logger = logging.getLogger(__name__)

FEATURES_FILE = PathUtils.get_features_file()


def get_features():
    if not os.path.isfile(FEATURES_FILE):
        return []
    with open(FEATURES_FILE) as f:
        return [line.strip() for line in f if line.strip()]


@click.command("webpack:compile", help="Compile webpack for one or all features.")
@click.argument("feature_name", required=False)
@click.option("--watch", is_flag=True, help="Enable watch mode for development.")
def webpack_compile(feature_name, watch):
    production = os.getenv("FLASK_ENV", "develop") == "production"

    def normalize_feature_name(name):
        return name if name.startswith("splent_feature_") else f"splent_feature_{name}"

    features = (
        [normalize_feature_name(feature_name)]
        if feature_name
        else get_features()
    )

    for feature in features:
        compile_feature(feature, watch, production)


def compile_feature(feature, watch, production):
    webpack_file = os.path.join("/workspace", feature, "src", feature, "assets", "js", "webpack.config.js")

    if not os.path.exists(webpack_file):
        click.echo(click.style(f"⚠ No webpack.config.js found in {feature}, skipping...", fg="yellow"))
        return

    click.echo(click.style(f"🚀 Compiling {feature}...", fg="cyan"))

    mode = "production" if production else "development"
    extra_flags = "--devtool source-map --no-cache" if not production else ""
    watch_flag = "--watch" if watch and not production else ""

    webpack_command = f"npx webpack --config {webpack_file} --mode {mode} {watch_flag} {extra_flags} --color"

    try:
        if watch:
            subprocess.Popen(webpack_command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            click.echo(click.style(f"👀 Watching {feature} in {mode} mode...", fg="blue"))
        else:
            subprocess.run(webpack_command, shell=True, check=True)
            click.echo(click.style(f"✅ Successfully compiled {feature} in {mode} mode!", fg="green"))
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"❌ Error compiling {feature}: {e}", fg="red"))
