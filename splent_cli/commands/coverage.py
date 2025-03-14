import click
import subprocess
import os

from splent_cli.utils.path_utils import PathUtils


@click.command(
    "coverage",
    help="Runs pytest coverage on the blueprints directory or a specific module.",
)
@click.argument("module_name", required=False)
@click.option(
    "--html", is_flag=True, help="Generates an HTML coverage report."
)
def coverage(module_name, html):
    modules_dir = PathUtils.get_modules_dir()
    test_path = modules_dir

    if module_name:
        test_path = os.path.join(modules_dir, module_name)
        if not os.path.exists(test_path):
            click.echo(
                click.style(
                    f"Module '{module_name}' does not exist.", fg="red"
                )
            )
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
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"Error running coverage: {e}", fg="red"))
