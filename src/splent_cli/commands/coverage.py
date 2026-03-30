import click
import subprocess
import os

from splent_cli.utils.path_utils import PathUtils
from splent_cli.services import context


@click.command(
    "coverage",
    short_help="Runs pytest coverage on the selected feature",
)
@click.argument("module_name", required=False)
@click.option("--html", is_flag=True, help="Generates an HTML coverage report.")
@context.requires_product
def coverage(module_name, html):
    modules_dir = PathUtils.get_modules_dir()
    test_path = modules_dir

    if module_name:
        test_path = os.path.join(modules_dir, module_name)
        if not os.path.exists(test_path):
            click.echo(click.style(f"Module '{module_name}' does not exist.", fg="red"))
            return
        click.echo(f"Running coverage for the '{module_name}' module...")
    else:
        click.echo("Running coverage for all modules...")

    coverage_cmd = [
        "pytest",
        "--ignore-glob=*selenium*",
        "--cov=" + test_path,
        test_path,
    ]

    if html:
        coverage_cmd.extend(["--cov-report", "html"])

    try:
        subprocess.run(coverage_cmd, check=True)
    except subprocess.CalledProcessError:
        click.echo(
            click.style("❌ Coverage run failed (tests may be failing).", fg="red")
        )
        raise SystemExit(1)
