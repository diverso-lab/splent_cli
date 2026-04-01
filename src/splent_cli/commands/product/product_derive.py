import json
import os
import subprocess
import tomllib

import click

from splent_cli.services import context, compose
from splent_cli.services.preflight import run_preflight
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.commands.product.product_sync import product_sync
from splent_cli.commands.product.product_env import product_env
from splent_cli.commands.product.product_up import product_up
from splent_cli.commands.product.product_run import product_runc
from splent_cli.commands.product.product_port import product_port
from splent_cli.commands.product.product_build import product_build
from splent_cli.commands.product.product_deploy import product_deploy


# ── Port conflict helpers ──────────────────────────────────────────────────────


def _extract_host_ports(compose_file: str) -> list[tuple[int, str]]:
    """Return [(host_port, service_name)] declared in a docker-compose file."""
    result = subprocess.run(
        ["docker", "compose", "-f", compose_file, "config", "--format", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []

    ports = []
    for svc_name, svc in config.get("services", {}).items():
        for port in svc.get("ports", []):
            published = port.get("published") if isinstance(port, dict) else None
            if published:
                try:
                    ports.append((int(published), svc_name))
                except (ValueError, TypeError):
                    pass
    return ports


def _containers_using_port(host_port: int) -> list[tuple[str, str]]:
    """Return [(container_id, container_name)] of running containers bound to host_port."""
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Ports}}"],
        capture_output=True,
        text=True,
    )
    conflicts = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        cid, name, ports_str = parts
        if f":{host_port}->" in ports_str:
            conflicts.append((cid, name))
    return conflicts


def _run_port_check(
    workspace: str, product: str, product_path: str, features: list, env: str
) -> list[dict]:
    """
    Scan all compose files that product:up will start and find port conflicts.
    Returns list of dicts: {port, service, containers: [(id, name)]}
    """
    conflicts = []
    seen_ports: set[int] = set()

    def check_file(label, compose_file):
        for host_port, svc_name in _extract_host_ports(compose_file):
            if host_port in seen_ports:
                continue
            seen_ports.add(host_port)
            blocking = _containers_using_port(host_port)
            if blocking:
                conflicts.append(
                    {
                        "port": host_port,
                        "service": f"{label}/{svc_name}",
                        "containers": blocking,
                    }
                )

    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        docker_dir = compose.feature_docker_dir(workspace, clean)
        for fname in [f"docker-compose.{env}.yml", "docker-compose.yml"]:
            cf = os.path.join(docker_dir, fname)
            if os.path.exists(cf):
                check_file(clean, cf)
                break

    for fname in [f"docker-compose.{env}.yml", "docker-compose.yml"]:
        cf = os.path.join(os.path.join(product_path, "docker"), fname)
        if os.path.exists(cf):
            check_file(product, cf)
            break

    return conflicts


@click.command(
    "product:derive",
    short_help="Derive and launch the active product (SPL derivation pipeline).",
)
@click.option("--dev", "mode", flag_value="dev", help="Derive in development mode.")
@click.option("--prod", "mode", flag_value="prod", help="Derive in production mode.")
def product_derive(mode):
    """
    Full SPL product derivation pipeline.

    \b
    Runs pre-flight checks before the pipeline:
      1. product:validate — feature selection must be satisfiable under the UVL model.
      2. feature:diff    — no ERROR-level conflicts between feature contracts.
      3. port conflicts  — no running containers occupying required host ports (dev only).

    \b
    --dev runs (after pre-flight):
      1. product:sync
      2. product:env --generate --all --dev
      3. product:env --merge --dev
      4. product:up --dev
      5. product:run --dev
      6. product:port

    \b
    --prod runs (after pre-flight):
      1. product:build   — merge env + compose + build Docker image
      2. product:deploy  — start production containers
    """
    if not mode:
        click.secho("  error: you must specify --dev or --prod.", fg="red")
        raise SystemExit(1)

    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    click.echo()
    click.echo(click.style(f"  Deriving {product} ({mode})", bold=True))
    click.echo()

    # ── Pre-flight checks ──────────────────────────────────────────────────
    preflight_failed = not run_preflight(interactive=True)

    # ports — port conflict check
    def _identify_env(container_name: str) -> str:
        """Guess whether a container belongs to dev or prod based on naming."""
        if "_deploy" in container_name:
            return "prod"
        return "dev"

    port_conflicts = []
    if mode == "dev":
        pyproject_path = os.path.join(product_dir, "pyproject.toml")
        features = []
        if os.path.exists(pyproject_path):
            with open(pyproject_path, "rb") as f:
                features = read_features_from_data(tomllib.load(f), mode)
        port_conflicts = _run_port_check(
            workspace, product, product_dir, features, mode
        )
    elif mode == "prod":
        deploy_compose = os.path.join(
            product_dir, "docker", "docker-compose.deploy.yml"
        )
        prod_compose = os.path.join(product_dir, "docker", "docker-compose.prod.yml")
        compose_to_check = None
        if os.path.isfile(deploy_compose):
            compose_to_check = deploy_compose
        elif os.path.isfile(prod_compose):
            compose_to_check = prod_compose
        if compose_to_check:
            for host_port, svc_name in _extract_host_ports(compose_to_check):
                blocking = _containers_using_port(host_port)
                if blocking:
                    port_conflicts.append(
                        {
                            "port": host_port,
                            "service": f"{product}/{svc_name}",
                            "containers": blocking,
                        }
                    )
        else:
            click.secho(
                "  ports    no compose file yet — will be generated by product:build",
                fg="bright_black",
            )

    if not port_conflicts:
        click.echo("  ports    no conflicts detected")
    else:
        click.secho(f"  ports    {len(port_conflicts)} conflict(s) found:", fg="yellow")
        all_containers: dict[str, str] = {}  # id → name
        for conflict in port_conflicts:
            for cid, cname in conflict["containers"]:
                all_containers[cid] = cname
            container_list = ", ".join(
                f"{n} ({_identify_env(n)})" for _, n in conflict["containers"]
            )
            click.secho(
                f"           port {conflict['port']:>5} <- {conflict['service']}"
                f"  (blocked by: {container_list})",
                fg="yellow",
            )

        # Show environment-specific hint
        blocking_envs_flat = set()
        for conflict in port_conflicts:
            for _, cname in conflict["containers"]:
                blocking_envs_flat.add(_identify_env(cname))
        if mode == "dev" and "prod" in blocking_envs_flat:
            click.secho(
                "           run 'splent product:down --prod' to stop the production deployment first",
                fg="bright_black",
            )
        elif mode == "prod" and "dev" in blocking_envs_flat:
            click.secho(
                "           run 'splent product:down --dev' to stop the development environment first",
                fg="bright_black",
            )

        click.echo()
        stop_them = click.confirm(
            "  Stop and remove the conflicting containers?", default=False
        )
        if stop_them:
            for cid, cname in all_containers.items():
                click.echo(f"  ports    stopping {cname}...")
                subprocess.run(["docker", "stop", cid], capture_output=True)
                subprocess.run(["docker", "rm", cid], capture_output=True)
            click.echo("  ports    conflicting containers removed")
        else:
            click.secho(
                "  ports    cannot proceed with conflicts unresolved",
                fg="red",
            )
            preflight_failed = True

    if preflight_failed:
        click.echo()
        click.secho(
            "  Pre-flight checks failed. Fix the issues above before deriving.",
            fg="red",
        )
        click.echo()
        raise SystemExit(1)

    click.echo()

    # ── Derivation pipeline ────────────────────────────────────────────────
    ctx = click.get_current_context()

    if mode == "dev":
        click.echo(
            click.style("  sync     ", dim=True) + "resolving feature symlinks..."
        )
        ctx.invoke(product_sync, force=False)

        click.echo(click.style("  env      ", dim=True) + "generating .env files")
        ctx.invoke(
            product_env, generate=True, merge=False, env_name="dev", process_all=True
        )

        click.echo(click.style("  env      ", dim=True) + "merging .env files")
        ctx.invoke(
            product_env, generate=False, merge=True, env_name="dev", process_all=False
        )

        click.echo(click.style("  up       ", dim=True) + "starting containers")
        ctx.invoke(product_up, dev=True, prod=False)

        click.echo(click.style("  run      ", dim=True) + "launching application")
        ctx.invoke(product_runc, env_dev=True, env_prod=False)

        ctx.invoke(product_port, env_flag="dev")

    elif mode == "prod":
        click.echo("  build    merging env + compose + Docker image")
        ctx.invoke(product_build, no_image=False, skip_preflight=True)

        click.echo("  deploy   starting production containers")
        ctx.invoke(product_deploy, down=False)

    click.echo()
    click.secho(f"  {product} ({mode}) derived successfully.", fg="green")
    click.echo()
