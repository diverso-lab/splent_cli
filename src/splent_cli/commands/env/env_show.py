import click
import subprocess
from splent_cli.services import context


def _mask(value: str, key: str) -> str:
    sensitive = ("TOKEN", "SECRET", "PASSWORD", "KEY", "API_KEY")
    if any(k in key.upper() for k in sensitive):
        return value[:6] + "..." + value[-4:] if len(value) > 10 else "********"
    return value


@click.command(
    "env:show",
    short_help="Compare .env file values against what is loaded in the shell.",
)
def env_show():
    """Check which variables from .env are actually available in the current Bash shell."""
    env_file = context.workspace() / ".env"

    if not env_file.exists():
        click.secho("⚠️  No .env file found.", fg="red")
        raise SystemExit(1)

    click.echo(f"📄 Reading variables from {env_file}\n")

    with open(env_file) as f:
        lines = [
            line.strip()
            for line in f.readlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    for line in lines:
        if "=" not in line:
            continue

        key, file_value = line.split("=", 1)
        key, file_value = key.strip(), file_value.strip().strip('"')

        # Run `echo $VAR` in bash to read the variable's live value from the shell environment
        try:
            result = subprocess.run(
                ["bash", "-c", f"echo ${key}"], capture_output=True, text=True
            )
            current_value = result.stdout.strip()
        except OSError:
            current_value = ""

        # Compare file value vs live shell value
        if not current_value:
            click.secho(f"⚠️  {key} not loaded", fg="yellow")
            click.echo(f"   .env: {_mask(file_value, key)}")
        elif current_value == file_value:
            click.secho(f"✅ {key} = {_mask(current_value, key)}", fg="green")
        else:
            click.secho(f"🟡 {key} loaded but differs", fg="yellow")
            click.echo(f"   shell: {_mask(current_value, key)}")
            click.echo(f"   .env : {_mask(file_value, key)}")

    click.echo("\n💡 Tip: reload environment variables with:")
    click.secho("   source .env", bold=True)
