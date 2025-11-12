import os
import click
from pathlib import Path


@click.command("env:set")
@click.argument("environment", type=click.Choice(["dev", "prod"], case_sensitive=False))
def env_set(environment):
    """Set SPLENT_ENV to 'dev' or 'prod' (updates .env and current session)."""
    workspace = "/workspace"
    env_file = Path(workspace) / ".env"

    # Ensure .env exists
    if not env_file.exists():
        click.echo("⚠️  No .env file found. Creating a new one...")
        env_file.write_text("")

    # Update or add SPLENT_ENV in .env
    lines = env_file.read_text().splitlines()
    new_lines = []
    updated = False
    for line in lines:
        if line.startswith("SPLENT_ENV="):
            new_lines.append(f"SPLENT_ENV={environment}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"SPLENT_ENV={environment}")

    env_file.write_text("\n".join(new_lines) + "\n")

    # Update current environment
    os.environ["SPLENT_ENV"] = environment

    # Fancy output
    color = "green" if environment == "dev" else "yellow"
    emoji = "🧩" if environment == "dev" else "🚀"
    click.secho(f"{emoji} SPLENT_ENV set to '{environment}'", fg=color, bold=True)
    click.echo(f"📄 Updated {env_file}")

    # Reminder
    click.secho("\n💡 Remember to reload environment variables:", fg="blue")
    click.secho("   source .env\n", bold=True)
