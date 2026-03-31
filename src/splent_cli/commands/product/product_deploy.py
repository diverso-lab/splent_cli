import os
import subprocess

import click
import yaml
from splent_cli.services import context


@click.command(
    "product:deploy",
    short_help="Deploy or stop the product using docker-compose.deploy.yml.",
)
@click.option("--down", is_flag=True, help="Stop the running deployment.")
@click.option("--ci", is_flag=True, help="Non-interactive mode for CI/CD pipelines.")
def product_deploy(down, ci):
    """
    Deploy the SPLENT product:

    \b
    - Ensures .env exists (creates it from .env.deploy.example if missing).
    - Prompts interactively for variables with <SET> values.
    - Writes final .env.
    - Executes `docker compose up -d` with docker-compose.deploy.yml.

    Use --down to stop a running deployment.
    """
    product = context.require_app()
    product_path = str(context.workspace() / product)
    docker_dir = os.path.join(product_path, "docker")

    compose_path = os.path.join(docker_dir, "docker-compose.deploy.yml")
    env_path = os.path.join(docker_dir, ".env.deploy")
    env_example_path = os.path.join(docker_dir, ".env.deploy.example")

    # ---------------------------------------------------------
    # --down: stop deployment
    # ---------------------------------------------------------
    if down:
        if not os.path.isfile(compose_path):
            click.secho("❌ docker-compose.deploy.yml not found.", fg="red")
            raise SystemExit(1)

        click.echo("🛑 Stopping deployment...\n")
        try:
            subprocess.run(
                ["docker", "compose", "-f", compose_path, "down"],
                check=True,
                cwd=docker_dir,
            )
            click.echo("\n✅ Deployment stopped.")
        except subprocess.CalledProcessError:
            click.secho("❌ Failed to stop deployment.", fg="red")
            raise SystemExit(1)
        return

    # ---------------------------------------------------------
    # Validate required build artifacts
    # ---------------------------------------------------------
    if not os.path.isfile(env_example_path):
        click.echo(
            "❌ .env.deploy.example not found. Run `splent product:build` first."
        )
        raise SystemExit(1)

    if not os.path.isfile(compose_path):
        click.echo(
            "❌ docker-compose.deploy.yml not found. Run `splent product:build` first."
        )
        raise SystemExit(1)

    # ---------------------------------------------------------
    # Create .env if missing
    # ---------------------------------------------------------
    if not os.path.isfile(env_path):
        click.echo("📄 .env not found → creating it from .env.deploy.example")
        with open(env_example_path, "r", encoding="utf-8") as src:
            content = src.read()
        with open(env_path, "w", encoding="utf-8") as dst:
            dst.write(content)
        click.echo("✅ Created .env")

    # ---------------------------------------------------------
    # Load .env and detect <SET>
    # ---------------------------------------------------------
    env_vars = {}
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env_vars[k] = v

    # ---------------------------------------------------------
    # Ask interactively for missing (<SET>) values
    # ---------------------------------------------------------
    missing_vars = []
    for key, value in env_vars.items():
        if value.strip() == "<SET>":
            if ci:
                env_value = os.getenv(key)
                if env_value:
                    env_vars[key] = env_value
                else:
                    missing_vars.append(key)
            else:
                new_value = click.prompt(
                    f"🔧 Value required for {key}", hide_input=False
                )
                env_vars[key] = new_value

    if missing_vars:
        click.secho(
            f"❌ Missing required variables: {', '.join(missing_vars)}\n"
            f"   Set them as environment variables or run without --ci for interactive mode.",
            fg="red",
        )
        raise SystemExit(1)

    # ---------------------------------------------------------
    # Save updated .env
    # ---------------------------------------------------------
    with open(env_path, "w", encoding="utf-8") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")

    click.echo("📝 Updated .env")

    # ---------------------------------------------------------
    # Deploy using docker compose
    # ---------------------------------------------------------
    click.echo("\n🐳 Deploying product...\n")

    try:
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                compose_path,
                "--env-file",
                env_path,
                "up",
                "-d",
            ],
            check=True,
        )

        # Show access URL
        app_port = None
        compose_data = yaml.safe_load(open(compose_path))
        for svc in compose_data.get("services", {}).values():
            for p in svc.get("ports", []):
                parts = str(p).split(":")
                if len(parts) == 2 and parts[1] == "5000":
                    app_port = parts[0]
                    break
            if app_port:
                break

        click.echo("\n🎯 Deployment successful!")
        if app_port:
            click.secho(
                f"🌐 App running at: http://localhost:{app_port}",
                fg="green",
                bold=True,
            )
    except subprocess.CalledProcessError as e:
        click.secho("❌ Deployment failed.", fg="red")
        if e.stderr:
            click.echo(e.stderr)
        raise SystemExit(1)


cli_command = product_deploy
