import click
import subprocess

from rosemary.utils.path_utils import PathUtils


@click.command("linter", help="Runs Ruff linter on the project.")
def linter():
    """Ejecuta Ruff para analizar el código en busca de errores."""
    directories = [
        PathUtils.get_app_dir(),
        PathUtils.get_rosemary_dir(),
        PathUtils.get_core_dir(),
    ]

    click.echo(
        click.style("\n📌 Running Ruff Linter...\n", fg="cyan", bold=True)
    )

    for directory in directories:
        click.echo(
            click.style(
                f"🔍 Checking {directory}...\n", fg="yellow", bold=True
            )
        )
        result = subprocess.run(
            ["ruff", "check", directory], capture_output=True, text=True
        )

        if result.returncode != 0:
            click.echo(click.style(result.stdout, fg="red"))
            click.echo(
                click.style(
                    f"❌ Issues found in {directory}.\n", fg="red", bold=True
                )
            )
        else:
            click.echo(
                click.style(
                    f"✅ No issues found in {directory}. 🎉\n",
                    fg="green",
                    bold=True,
                )
            )

    click.echo(
        click.style("✔️ Linter check completed!\n", fg="cyan", bold=True)
    )


@click.command(
    "linter:fix",
    help="Automatically formats and fixes code using Ruff and Black.",
)
def linter_fix():
    """Ejecuta Ruff en modo 'fix' y luego Black para formateo."""
    directories = [
        PathUtils.get_app_dir(),
        PathUtils.get_rosemary_dir(),
        PathUtils.get_core_dir(),
    ]

    click.echo(
        click.style(
            "\n📌 Running Ruff Fix & Black Formatter...\n",
            fg="cyan",
            bold=True,
        )
    )

    fixes_applied = 0

    for directory in directories:
        click.echo(
            click.style(
                f"🔧 Fixing {directory} with Ruff...\n", fg="yellow", bold=True
            )
        )
        result = subprocess.run(
            ["ruff", "check", "--fix", directory],
            capture_output=True,
            text=True,
        )

        if "Fixed" in result.stdout:
            click.echo(click.style(result.stdout, fg="blue"))
            fixes_applied += 1
        else:
            click.echo(
                click.style(
                    f"✅ No fixes needed in {directory}. 🎉\n",
                    fg="green",
                    bold=True,
                )
            )

        click.echo(
            click.style(
                f"🎨 Formatting {directory} with Black...\n",
                fg="yellow",
                bold=True,
            )
        )
        black_result = subprocess.run(
            ["black", "--line-length=79", directory],
            capture_output=True,
            text=True,
        )

        if black_result.returncode == 0:
            click.echo(
                click.style(
                    f"✨ Code in {directory} formatted successfully!\n",
                    fg="green",
                    bold=True,
                )
            )
        else:
            click.echo(
                click.style(
                    f"❌ Failed to format {directory} with Black.\n",
                    fg="red",
                    bold=True,
                )
            )

    click.echo(
        click.style("\n✔️ Fix & Formatting Completed!", fg="cyan", bold=True)
    )

    if fixes_applied > 0:
        click.echo(
            click.style(
                f"🔧 Ruff applied fixes in {fixes_applied} directories!",
                fg="blue",
                bold=True,
            )
        )
    else:
        click.echo(
            click.style(
                "🚀 No fixes were needed! Your code is already clean! 🎉",
                fg="green",
                bold=True,
            )
        )
