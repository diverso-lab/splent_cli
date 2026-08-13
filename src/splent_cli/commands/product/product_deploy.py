import os
import re
import subprocess

import click

from splent_cli.utils.io_utils import env_line
import yaml
from splent_cli.services import compose, context
from splent_cli.commands.product.product_build import (
    USER_TUNABLE_COMMENT,
    load_env_file_with_markers,
)
from splent_cli.utils.proc import require_docker

# Env vars whose value is a directory the running app writes to. Their contents
# only survive a redeploy if a volume in the compose file mounts that exact
# path, so product:deploy warns when one is set without a matching mount.
PERSISTENCE_DIR_VARS = ("PROTECTED_UPLOADS_DIR", "ARCHIVE_DIR")

# ${VAR} and ${VAR:-default} as they appear in compose mount targets.
_COMPOSE_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")


def _expand_compose_vars(value: str, env_vars: dict) -> str:
    """Resolve ${VAR} / ${VAR:-default} in a compose value against env_vars."""

    def replace(match):
        name, default = match.group(1), match.group(2)
        resolved = env_vars.get(name)
        if resolved:
            return resolved
        return default or ""

    return _COMPOSE_VAR_RE.sub(replace, value)


def _mount_targets(compose_data: dict, env_vars: dict) -> set:
    """Every container-side path mounted by any service in the compose file."""
    targets = set()
    for svc in compose_data.get("services", {}).values():
        for vol in svc.get("volumes", []) or []:
            if isinstance(vol, dict):
                target = _expand_compose_vars(str(vol.get("target", "")), env_vars)
            else:
                # "source:target" or "source:target:mode". Interpolate first:
                # a ${VAR:-default} target contains ":" of its own, so
                # splitting the raw string would cut the path in half.
                expanded = _expand_compose_vars(str(vol), env_vars)
                parts = expanded.split(":")
                target = parts[1] if len(parts) >= 2 else ""
            if target:
                targets.add(target.rstrip("/"))
    return targets


def _warn_unmounted_persistence_dirs(compose_path: str, env_vars: dict) -> None:
    """Warn about directories the app writes to that no volume preserves.

    A product scaffolded before the persistence volumes existed can pick up
    these variables from a synced .env.prod.example while its compose file
    still has no matching volume. The app would then write to a path that the
    next redeploy throws away, silently.
    """
    try:
        with open(compose_path, "r", encoding="utf-8") as cf:
            compose_data = yaml.safe_load(cf) or {}
    except (OSError, yaml.YAMLError):
        return

    targets = _mount_targets(compose_data, env_vars)

    for var in PERSISTENCE_DIR_VARS:
        value = (env_vars.get(var) or "").strip()
        if not value or value.rstrip("/") in targets:
            continue
        click.secho(
            f"  {var} is set to '{value}' but no volume in "
            f"docker-compose.deploy.yml mounts that path.\n"
            f"  Files written there are lost on every redeploy. Add a named "
            f"volume for it to the product's docker/docker-compose.prod.yml "
            f"and run 'splent product:build' again,\n"
            f"  or remove {var} from .env.deploy if the app does not use it.",
            fg="yellow",
        )


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

        require_docker()

        click.echo(click.style("  stopping ", dim=True) + f"{product} (prod)...")
        try:
            # Same project and same env file the deploy used, so this stops
            # what it started and nothing else. Without --env-file, Compose
            # would read the development .env sitting in this directory and
            # resolve the network to a different name than the one running.
            cmd = ["docker", "compose", "-p", compose.deploy_project_name(product)]
            if os.path.isfile(env_path):
                cmd += ["--env-file", env_path]
            subprocess.run(
                cmd + ["-f", compose_path, "down"],
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
    # Create or sync .env.deploy from template
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
    # Sync .env.deploy with template
    # ---------------------------------------------------------
    # Strategy: the template (.env.deploy.example) is the source of truth
    # for all infrastructure variables. Two kinds of value belong to the
    # operator instead and are never overwritten:
    #   * secrets and other keys the template ships as <SET>, once filled in;
    #   * keys the template marks with a "# user-tunable" comment, such as the
    #     interface a port binds to. A marked key the operator deleted stays
    #     deleted, so removing it to fall back to the compose default works.
    # Everything else is updated from the template on every deploy.
    existing_vars, _ = load_env_file_with_markers(env_path)
    example_vars, tunable_keys = load_env_file_with_markers(env_example_path)

    # Keys the operator removed on purpose. Only meaningful for tunables: a
    # template-owned key that went missing is still restored below.
    deleted_tunables = {
        k for k in tunable_keys if k in example_vars and k not in existing_vars
    }

    # Build final env: start from template, preserve user-configured values
    env_vars = {}
    updated = []
    for k, v in example_vars.items():
        if k in deleted_tunables:
            # The operator deleted this tunable. Leave it out and let the
            # compose default apply.
            continue
        elif k in tunable_keys and k in existing_vars:
            # Operator-owned value — keep it exactly as they wrote it.
            env_vars[k] = existing_vars[k]
        elif (
            v.strip() == "<SET>"
            and k in existing_vars
            and existing_vars[k].strip() != "<SET>"
        ):
            # User already configured this — keep their value
            env_vars[k] = existing_vars[k]
        elif k in existing_vars and existing_vars[k] != v:
            # Infrastructure value changed in template — update it
            env_vars[k] = v
            updated.append(k)
        else:
            env_vars[k] = v

    if updated:
        click.echo(
            click.style("  env      ", dim=True)
            + f"updated {len(updated)} variable(s) from template"
        )

    if deleted_tunables:
        click.echo(
            click.style("  env      ", dim=True)
            + f"keeping {len(deleted_tunables)} removed variable(s) out: "
            + ", ".join(sorted(deleted_tunables))
        )

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
    # The marker comments are carried over so the file itself tells the
    # operator which keys are theirs to edit.
    with open(env_path, "w", encoding="utf-8") as f:
        for k, v in env_vars.items():
            if k in tunable_keys:
                f.write(f"{USER_TUNABLE_COMMENT}\n")
            f.write(env_line(k, v))

    # ---------------------------------------------------------
    # Warn about write directories no volume preserves
    # ---------------------------------------------------------
    _warn_unmounted_persistence_dirs(compose_path, env_vars)

    # ---------------------------------------------------------
    # Update product .env with SPLENT_ENV=prod
    # ---------------------------------------------------------
    product_env_path = os.path.join(docker_dir, ".env")
    if os.path.isfile(product_env_path):
        lines = []
        found = False
        with open(product_env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("SPLENT_ENV="):
                    lines.append("SPLENT_ENV=prod\n")
                    found = True
                else:
                    lines.append(line)
        if not found:
            lines.append("SPLENT_ENV=prod\n")
        with open(product_env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

    # ---------------------------------------------------------
    # Ensure docker is available before any container operations
    # ---------------------------------------------------------
    require_docker()

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

    legacy = compose.legacy_deploy_project_notice(product)
    if legacy:
        click.secho(legacy, fg="yellow")
        click.echo()

    # The network the product's containers meet on, one per product. Declared
    # external in every compose file, so it must exist before the first
    # container starts.
    compose.ensure_network(compose.network_name(product))

    click.echo(click.style("  deploying ", dim=True) + f"{product} (prod)...")

    try:
        subprocess.run(
            [
                "docker",
                "compose",
                # Without this, Compose names the project after the directory
                # holding the compose file, which is 'docker' in every product.
                "-p",
                compose.deploy_project_name(product),
                "-f",
                compose_path,
                "--env-file",
                env_path,
                "up",
                "-d",
                "--build",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=docker_dir,
        )

        # Show access URL
        app_port = None
        with open(compose_path) as cf:
            compose_data = yaml.safe_load(cf)
        for svc in compose_data.get("services", {}).values():
            for p in svc.get("ports", []):
                # Entries may be "5123:5000" or "<bind host>:5123:5000", and
                # the bind host may itself contain ":" when written as
                # ${VAR:-default}, so read the mapping from the right-hand end.
                parts = str(p).split(":")
                if len(parts) >= 2 and parts[-1] == "5000":
                    app_port = parts[-2]
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
            # The production web container is named ``<product>_web_deploy`` —
            # NOT ``<product>_web`` (that is the dev container). A cold start runs
            # migrations and compiles assets before gunicorn binds, so allow a
            # generous window (~90s) before declaring the app unhealthy.
            for attempt in range(45):
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
                click.echo(f"    docker logs {product}_web")
        else:
            click.secho("  done.", fg="green")
    except subprocess.CalledProcessError as e:
        click.secho("  Deployment failed.", fg="red")
        output = (e.stderr or e.stdout or "").strip()
        if output:
            for line in output.splitlines():
                click.echo(f"    {line}")
        raise SystemExit(1)


cli_command = product_deploy
