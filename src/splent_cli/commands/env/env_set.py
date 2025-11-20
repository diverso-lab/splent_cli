import click
from pathlib import Path

WORKSPACE_ENV = Path("/workspace/.env")


# -------------------------
# UTILS
# -------------------------

def load_env():
    """Return dict with all key=value from .env."""
    if not WORKSPACE_ENV.exists():
        WORKSPACE_ENV.write_text("")
        return {}
    data = {}
    for line in WORKSPACE_ENV.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def write_env(env: dict):
    """Write dict back to .env."""
    lines = [f"{k}={v}" for k, v in env.items()]
    WORKSPACE_ENV.write_text("\n".join(lines) + "\n")


def set_var(key: str, value: str):
    """Helper to update a single key in .env."""
    env = load_env()
    env[key] = value
    write_env(env)

    
def remind_source():
    click.secho("\n💡 Remember to reload environment variables:", fg="blue")
    click.secho("   source .env\n")


# -------------------------
# INTERNAL HANDLERS (reusables)
# -------------------------

def set_mode_interactive():
    click.echo("Select execution mode for SPLENT:")
    mode = click.prompt(
        "Enter 'dev' or 'prod'",
        type=click.Choice(["dev", "prod"], case_sensitive=False)
    )
    set_var("SPLENT_MODE", mode)
    click.secho(f"✔ SPLENT_MODE set to {mode}", fg="green")
    remind_source()


def set_github_interactive():
    user = click.prompt("GitHub username", type=str)
    token = click.prompt("GitHub personal access token", hide_input=True)

    set_var("GITHUB_USER", user)
    set_var("GITHUB_TOKEN", token)

    click.secho("✔ GitHub configuration updated.", fg="green")
    remind_source()


def set_pypi_interactive():
    username = "__token__"
    token = click.prompt("PyPI token", hide_input=True)

    set_var("PYPI_USERNAME", username)
    set_var("PYPI_TOKEN", token)

    click.secho("✔ PyPI credentials updated.", fg="green")
    remind_source()


def set_developer_interactive():
    click.echo("Enable SSH usage for SPLENT feature development?")

    answer = click.prompt(
        "(y/n)",
        type=click.Choice(["y", "n"], case_sensitive=False)
    )
    enabled = "true" if answer == "y" else "false"

    set_var("SPLENT_DEVELOPER_SSH", enabled)

    click.secho(f"✔ SPLENT_DEVELOPER_SSH set to {enabled}", fg="green")
    remind_source()


# -------------------------
# ROOT COMMAND
# -------------------------

@click.group("env:set", short_help="Set environment variables interactively")
@click.option("--wizard", is_flag=True, help="Run interactive environment setup wizard.")
@click.pass_context
def env_set_group(ctx, wizard):
    """Base command for environment configuration."""
    if wizard:
        run_wizard()
        ctx.exit()


# -------------------------
# SUBCOMMANDS (simple wrappers)
# -------------------------

@env_set_group.command("mode")
def env_set_mode():
    set_mode_interactive()


@env_set_group.command("github")
def env_set_github():
    set_github_interactive()


@env_set_group.command("pypi")
def env_set_pypi():
    set_pypi_interactive()


@env_set_group.command("developer")
def env_set_developer():
    set_developer_interactive()


# -------------------------
# WIZARD
# -------------------------

def run_wizard():
    while True:
        click.echo("\n=== SPLENT ENV CONFIG WIZARD ===")
        click.echo("1. Set mode (dev/prod)")
        click.echo("2. Configure GitHub credentials")
        click.echo("3. Configure PyPI token")
        click.echo("4. Configure developer SSH mode")
        click.echo("5. Exit")
        click.echo("--------------------------------")

        choice = click.prompt("Choose an option", type=int)

        if choice == 1:
            set_mode_interactive()
        elif choice == 2:
            set_github_interactive()
        elif choice == 3:
            set_pypi_interactive()
        elif choice == 4:
            set_developer_interactive()
        elif choice == 5:
            click.echo("Leaving wizard.")
            break
        else:
            click.secho("Invalid option. Try again.", fg="red")
