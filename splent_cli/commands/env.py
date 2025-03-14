# splent_cli/commands/env.py

import click
from dotenv import dotenv_values

from splent_cli.utils.path_utils import PathUtils


@click.command()
def env():
    """Displays the current .env file values."""
    # Load the .env file
    env_dir = PathUtils.get_env_dir()
    env_values = dotenv_values(env_dir)

    # Display keys and values
    for key, value in env_values.items():
        click.echo(f"{key}={value}")
