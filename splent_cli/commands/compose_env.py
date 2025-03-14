import os
import click
from dotenv import dotenv_values
from flask.cli import with_appcontext

from splent_cli.utils.path_utils import PathUtils


@click.command(
    "compose:env",
    help="Combines .env files from blueprints with the root .env, checking for conflicts.",
)
@with_appcontext
def compose_env():

    modules_dir = PathUtils.get_modules_dir()
    root_env_path = PathUtils.get_env_dir()

    # Loads the current root .env variables into a dictionary
    root_env_vars = dotenv_values(root_env_path)

    # Finds and processes all blueprints .env files
    module_env_paths = [
        os.path.join(root, ".env")
        for root, dirs, files in os.walk(modules_dir)
        if ".env" in files
    ]
    for env_path in module_env_paths:
        blueprint_env_vars = dotenv_values(env_path)
        # Add or update the blueprint variables in the root .env dictionary
        for key, value in blueprint_env_vars.items():
            if key in root_env_vars and root_env_vars[key] != value:
                conflict_msg = (
                    f"Conflict found for variable '{key}' in {env_path}. "
                    "Keeping the original value."
                )
                click.echo(click.style(conflict_msg, fg="yellow"))
                continue
            root_env_vars[key] = value

    # Write back to the root .env file
    with open(root_env_path, "w") as root_env_file:
        for key, value in root_env_vars.items():
            root_env_file.write(f"{key}={value}\n")

    click.echo(
        click.style(
            "Successfully merged .env files without conflicts.", fg="green"
        )
    )
