import os
import click

from splent_cli.utils.path_utils import PathUtils
from splent_cli.utils.proc import run
from splent_cli.services import context


@click.command("selenium", help="Executes Selenium tests based on the environment.")
@click.argument("module", required=False)
@context.requires_product
def selenium(module):
    # Absolute paths
    working_dir = str(context.workspace())
    modules_dir = PathUtils.get_modules_dir()

    def validate_module(module):
        """Check if the module exists."""
        if module:
            module_path = os.path.join(modules_dir, module)
            if not os.path.exists(module_path):
                raise click.UsageError(f"Module '{module}' does not exist.")
            selenium_test_path = os.path.join(module_path, "tests", "test_selenium.py")
            if not os.path.exists(selenium_test_path):
                raise click.UsageError(
                    f"Selenium test for module '{module}' does not exist at path "
                    f"'{selenium_test_path}'."
                )

    def run_selenium_tests_in_local(module):
        """Run the Selenium tests."""
        if module:
            selenium_test_path = os.path.join(
                modules_dir, module, "tests", "test_selenium.py"
            )
            selenium_test_paths = [selenium_test_path]
        else:
            selenium_test_paths = []
            for module in os.listdir(modules_dir):
                tests_dir = os.path.join(modules_dir, module, "tests")
                selenium_test_path = os.path.join(tests_dir, "test_selenium.py")
                if os.path.exists(selenium_test_path):
                    selenium_test_paths.append(selenium_test_path)

        if not selenium_test_paths:
            click.secho("No Selenium tests found to run.", fg="yellow")
            return

        test_command = ["python"] + selenium_test_paths
        click.echo(f"Running Selenium tests with command: {' '.join(test_command)}")
        result = run(
            test_command,
            check=False,
            tool_hint="Install Python 3 and make sure 'python' is on your PATH.",
        )
        if result.returncode != 0:
            click.secho("❌ Selenium tests failed.", fg="red")
            raise SystemExit(1)

    # Validate module if provided
    if module:
        validate_module(module)

    if working_dir == "/workspace":
        click.echo(
            click.style(
                "Currently it is not possible to run this "
                "command from a Docker environment, do you want to implement it yourself? ^^",
                fg="red",
            )
        )

    elif working_dir == "/vagrant":
        click.echo(
            click.style(
                "Currently it is not possible to run this "
                "command from a Vagrant environment, do you want to implement it yourself? ^^",
                fg="red",
            )
        )

    else:
        run_selenium_tests_in_local(module)
