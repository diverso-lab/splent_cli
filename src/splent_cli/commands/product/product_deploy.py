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
    - Checks for port conflicts before starting containers.
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
            click.secho("  docker-compose.deploy.yml not found.", fg="red")
            raise SystemExit(1)

        click.echo(click.style("  stopping ", dim=True) + f"{product} (prod)...")
        try:
            subprocess.run(
                ["docker", "compose", "-f", compose_path, "down"],
                check=True,
                capture_output=True,
                cwd=docker_dir,
            )
            click.secho("  done.", fg="green")
        except subprocess.CalledProcessError:
            click.secho("  Failed to stop deployment.", fg="red")
            raise SystemExit(1)
        return

    # ---------------------------------------------------------
    # Validate required build artifacts
    # ---------------------------------------------------------
    if not os.path.isfile(env_example_path):
        click.secho(
            "  .env.deploy.example not found. Run 'splent product:build' first.",
            fg="red",
        )
        raise SystemExit(1)

    if not os.path.isfile(compose_path):
        click.secho(
            "  docker-compose.deploy.yml not found. Run 'splent product:build' first.",
            fg="red",
        )
        raise SystemExit(1)

    # ---------------------------------------------------------
    # Create .env.deploy if missing
    # ---------------------------------------------------------
    if not os.path.isfile(env_path):
        click.echo(
            click.style("  env      ", dim=True) + "creating .env.deploy from template"
        )
        with open(env_example_path, "r", encoding="utf-8") as src:
            content = src.read()
        with open(env_path, "w", encoding="utf-8") as dst:
            dst.write(content)

    # ---------------------------------------------------------
    # Load .env.deploy and detect <SET>
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
                    f"  Value required for {key}", hide_input=False
                )
                env_vars[key] = new_value

    if missing_vars:
        click.secho(
            f"  Missing required variables: {', '.join(missing_vars)}\n"
            f"  Set them as environment variables or run without --ci.",
            fg="red",
        )
        raise SystemExit(1)

    # ---------------------------------------------------------
    # Save updated .env.deploy
    # ---------------------------------------------------------
    with open(env_path, "w", encoding="utf-8") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")

    # ---------------------------------------------------------
    # Port conflict check
    # ---------------------------------------------------------
    from splent_cli.commands.product.product_derive import (
        _extract_host_ports,
        _containers_using_port,
    )

    port_conflicts = []
    for host_port, svc_name in _extract_host_ports(compose_path):
        blocking = _containers_using_port(host_port)
        if blocking:
            port_conflicts.append(
                {"port": host_port, "service": svc_name, "containers": blocking}
            )

    if port_conflicts:
        click.secho(f"  {len(port_conflicts)} port conflict(s) found:", fg="yellow")
        all_containers: dict[str, str] = {}
        for conflict in port_conflicts:
            for cid, cname in conflict["containers"]:
                all_containers[cid] = cname
            container_list = ", ".join(n for _, n in conflict["containers"])
            click.secho(
                f"    port {conflict['port']:>5} <- {conflict['service']}"
                f"  (blocked by: {container_list})",
                fg="yellow",
            )
        click.echo()
        if click.confirm(
            "  Stop and remove the conflicting containers?", default=False
        ):
            for cid, cname in all_containers.items():
                click.echo(f"  stopping {cname}...")
                subprocess.run(["docker", "stop", cid], capture_output=True)
                subprocess.run(["docker", "rm", cid], capture_output=True)
        else:
            click.secho("  Cannot proceed with conflicts unresolved.", fg="red")
            raise SystemExit(1)

    # ---------------------------------------------------------
    # Deploy using docker compose
    # ---------------------------------------------------------
    click.echo()
    click.echo(click.style("  deploying ", dim=True) + f"{product} (prod)...")

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
                "--build",
            ],
            check=True,
            cwd=docker_dir,
        )

        # Show access URL
        app_port = None
        with open(compose_path) as cf:
            compose_data = yaml.safe_load(cf)
        for svc in compose_data.get("services", {}).values():
            for p in svc.get("ports", []):
                parts = str(p).split(":")
                if len(parts) == 2 and parts[1] == "5000":
                    app_port = parts[0]
                    break
            if app_port:
                break

        click.echo()
        if app_port:
            url = f"http://localhost:{app_port}"

            # Health check — wait for the app to respond
            click.echo(
                click.style("  health   ", dim=True) + "waiting for app to respond..."
            )
            import time

            healthy = False
            for attempt in range(15):
                try:
                    result = subprocess.run(
                        [
                            "docker",
                            "exec",
                            f"{product}_web_deploy",
                            "bash",
                            "-c",
                            "curl -s -o /dev/null -w '%{http_code}' http://localhost:5000/",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    code = result.stdout.strip()
                    if code in ("200", "302"):
                        healthy = True
                        break
                except (subprocess.TimeoutExpired, Exception):
                    pass
                time.sleep(2)

            if healthy:
                click.echo(
                    click.style("  health   ", dim=True)
                    + click.style(f"HTTP {code}", fg="green")
                )
                click.echo()
                click.echo(
                    click.style("  URL: ", bold=True)
                    + click.style(url, fg="cyan", bold=True)
                )
                click.secho("  done.", fg="green")
            else:
                click.echo(
                    click.style("  health   ", dim=True)
                    + click.style("app not responding", fg="red")
                )
                click.echo()
                click.echo(
                    click.style("  URL: ", bold=True)
                    + click.style(url, fg="cyan", bold=True)
                )
                click.secho(
                    "  deployed but app may not be healthy. Check logs with:",
                    fg="yellow",
                )
                click.echo(f"    docker logs {product}_web_deploy")
        else:
            click.secho("  done.", fg="green")
    except subprocess.CalledProcessError as e:
        click.secho("  Deployment failed.", fg="red")
        if e.stderr:
            for line in e.stderr.strip().splitlines():
                click.echo(f"    {line}")
        raise SystemExit(1)


cli_command = product_deploy
