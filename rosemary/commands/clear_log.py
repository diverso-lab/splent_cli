import click
import os

from rosemary.utils.path_utils import PathUtils


@click.command("clear:log", help="Clears the 'app.log' file.")
def clear_log():
    log_file_path = PathUtils.get_app_log_dir()

    # Check if the log file exists
    if os.path.exists(log_file_path):
        try:
            # Deletes the log file
            os.remove(log_file_path)
            click.echo(
                click.style(
                    "The 'app.log' file has been successfully cleared.",
                    fg="green",
                )
            )
        except Exception as e:
            click.echo(
                click.style(
                    f"Error clearing the 'app.log' file: {e}", fg="red"
                )
            )
    else:
        click.echo(
            click.style("The 'app.log' file does not exist.", fg="yellow")
        )
