import subprocess
import click
from splent_cli.utils.path_utils import PathUtils


@click.command("linter", help="Run Ruff to lint and optionally fix the project.")
@click.option("--fix", is_flag=True, help="Automatically fix issues.")
@click.option("--format", is_flag=True, help="Format code with Ruff formatter.")
def linter(fix, format):
    """Run Ruff for linting and formatting (modern unified workflow)."""
    directories = [
        PathUtils.get_app_dir(),
        PathUtils.get_splent_cli_dir(),
        PathUtils.get_core_dir(),
    ]

    click.echo(click.style("\n📦 SPLENT Linter (Ruff unified)\n", fg="cyan", bold=True))

    base_command = ["ruff", "check"]
    if fix:
        base_command.append("--fix")

    for directory in directories:
        click.echo(click.style(f"🔍 Checking {directory}...\n", fg="yellow"))
        result = subprocess.run(
            base_command + [directory], capture_output=True, text=True
        )

        if result.returncode != 0:
            click.echo(click.style(result.stdout, fg="red"))
            click.echo(
                click.style(f"❌ Issues found in {directory}\n", fg="red", bold=True)
            )
        else:
            click.echo(click.style(f"✅ {directory} clean.\n", fg="green"))

    if format:
        click.echo(click.style("\n🎨 Formatting code with Ruff...\n", fg="blue"))
        for directory in directories:
            subprocess.run(["ruff", "format", directory])
        click.echo(click.style("✨ Code formatted successfully!\n", fg="green"))

    click.echo(click.style("✔️ Linter check completed!\n", fg="cyan", bold=True))
