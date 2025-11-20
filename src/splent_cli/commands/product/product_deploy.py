import os
import click
import subprocess


@click.command(
    "product:deploy",
    short_help="Deploy the product using docker-compose.deploy.yml and .env.",
)
def product_deploy():
    """
    Deploy the SPLENT product:

    - Ensures .env exists (creates it from .env.deploy.example if missing).
    - Prompts interactively for variables with <SET> values.
    - Writes final .env.
    - Executes `docker compose up -d` with docker-compose.deploy.yml.
    """
    product = os.getenv("SPLENT_APP")
    if not product:
        click.echo("❌ SPLENT_APP not set.")
        raise SystemExit(1)

    workspace = "/workspace"
    product_path = os.path.join(workspace, product)
    docker_dir = os.path.join(product_path, "docker")

    env_example_path = os.path.join(docker_dir, ".env.deploy.example")
    env_path = os.path.join(docker_dir, ".env")
    compose_path = os.path.join(docker_dir, "docker-compose.deploy.yml")

    # ---------------------------------------------------------
    # Validate required build artifacts
    # ---------------------------------------------------------
    if not os.path.isfile(env_example_path):
        click.echo("❌ .env.deploy.example not found. Run `splent product:build` first.")
        raise SystemExit(1)

    if not os.path.isfile(compose_path):
        click.echo("❌ docker-compose.deploy.yml not found. Run `splent product:build` first.")
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
    for key, value in env_vars.items():
        if value.strip() == "<SET>":
            new_value = click.prompt(f"🔧 Value required for {key}", hide_input=False)
            env_vars[key] = new_value

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
        click.echo("🎯 Deployment successful!")
    except subprocess.CalledProcessError as e:
        click.echo("❌ Deployment failed.")
        click.echo(e)
        raise SystemExit(1)
