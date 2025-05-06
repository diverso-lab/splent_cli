import logging
import click
import os
import subprocess

from splent_cli.utils.path_utils import PathUtils

logger = logging.getLogger(__name__)

MODULES_DIR = PathUtils.get_modules_dir()
EXCLUDED_MODULES = {".pytest_cache", "__pycache__"}


def load_excluded_modules():
    """Load additional excluded modules from .featureignore file."""
    moduleignore_path = os.path.join(os.getenv("WORKING_DIR", ""), ".featureignore")
    if os.path.exists(moduleignore_path):
        with open(moduleignore_path) as f:
            EXCLUDED_MODULES.update(filter(None, map(str.strip, f)))
    return EXCLUDED_MODULES


EXCLUDED_MODULES = load_excluded_modules()


@click.command("webpack:compile", help="Compile webpack for one or all modules.")
@click.argument("module_name", required=False)
@click.option("--watch", is_flag=True, help="Enable watch mode for development.")
def webpack_compile(module_name, watch):
    """Run webpack for a specific module or all modules."""
    production = os.getenv("FLASK_ENV", "develop") == "production"

    modules = (
        [module_name]
        if module_name
        else [m for m in os.listdir(MODULES_DIR) if os.path.isdir(os.path.join(MODULES_DIR, m)) and m not in EXCLUDED_MODULES]
    )

    for module in modules:
        compile_module(module, watch, production)


def compile_module(module, watch, production):
    """Compile a single module using webpack."""
    module_path = os.path.join(MODULES_DIR, module)
    webpack_file = os.path.join(module_path, "assets", "js", "webpack.config.js")

    if not os.path.exists(webpack_file):
        click.echo(click.style(f"⚠ No webpack.config.js found in {module}, skipping...", fg="yellow"))
        return

    click.echo(click.style(f"🚀 Compiling {module}...", fg="cyan"))

    mode = "production" if production else "development"
    extra_flags = "--devtool source-map --no-cache" if not production else ""
    watch_flag = "--watch" if watch and not production else ""

    webpack_command = f"npx webpack --config {webpack_file} --mode {mode} {watch_flag} {extra_flags} --color"

    try:
        if watch:
            subprocess.Popen(webpack_command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            click.echo(click.style(f"👀 Watching {module} in {mode} mode...", fg="blue"))
        else:
            subprocess.run(webpack_command, shell=True, check=True)
            click.echo(click.style(f"✅ Successfully compiled {module} in {mode} mode!", fg="green"))
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"❌ Error compiling {module}: {e}", fg="red"))
