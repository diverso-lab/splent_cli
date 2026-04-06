import os
import subprocess
import json
import tomllib

import click
from splent_cli.services import compose, context
from splent_cli.utils.feature_utils import read_features_from_data


def _status_color(state: str) -> str:
    state_l = state.lower()
    if "running" in state_l:
        return click.style(state, fg="green")
    if "exited" in state_l or "stopped" in state_l:
        return click.style(state, fg="red")
    if "starting" in state_l or "restarting" in state_l:
        return click.style(state, fg="yellow")
    if "unhealthy" in state_l:
        return click.style(state, fg="red")
    if "healthy" in state_l:
        return click.style(state, fg="green")
    return state


def _get_containers(project_name: str, compose_file: str) -> list[dict]:
    """Run docker compose ps and return parsed container list."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            compose_file,
            "ps",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    containers = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return containers


def _service_label(
    source: str, service: str, target_port: int
) -> tuple[str, str] | None:
    """Return (label, kind) if this port is worth showing, else None.

    kind is "http" (browser-accessible) or "tcp" (connection endpoint).
    """
    # Known HTTP target ports
    if target_port == 5000:
        return "App", "http"
    if target_port == 8025:
        return "Mailhog", "http"
    if target_port == 80 and "phpmyadmin" in service:
        return "phpMyAdmin", "http"
    if target_port in (80, 443) and "nginx" in service:
        return "Nginx", "http"
    if target_port in (80, 443, 8080, 3000, 4000):
        return (source or service), "http"
    # Known TCP services
    if target_port == 6379:
        return "Redis", "tcp"
    if target_port == 3306:
        return "MariaDB", "tcp"
    if target_port == 5432:
        return "PostgreSQL", "tcp"
    if target_port == 27017:
        return "MongoDB", "tcp"
    if target_port == 1025:
        return "Mailhog SMTP", "tcp"
    return None


def _format_ports(ports) -> str:
    if isinstance(ports, list):
        return ", ".join(
            f"{p.get('PublishedPort', '')}→{p.get('TargetPort', '')}"
            for p in ports
            if p.get("PublishedPort")
        )
    return str(ports) if ports else "—"


@click.command(
    "product:containers",
    short_help="List Docker container status for the active product and its features.",
)
@click.option("--dev", "env_dev", is_flag=True, help="Use development environment.")
@click.option("--prod", "env_prod", is_flag=True, help="Use production environment.")
def product_docker(env_dev, env_prod):
    """Shows the status of all Docker containers for the active product and its features."""
    if env_dev and env_prod:
        click.secho("❌ Cannot specify both --dev and --prod.", fg="red")
        raise SystemExit(1)

    product = context.require_app()
    workspace = str(context.workspace())

    # If no flag given, detect SPLENT_ENV from the running web container
    if not env_dev and not env_prod:
        try:
            result = subprocess.run(
                ["docker", "exec", f"{product}_web", "printenv", "SPLENT_ENV"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            detected = result.stdout.strip()
            if detected in ("dev", "prod"):
                env = detected
            else:
                env = "dev"
        except (subprocess.TimeoutExpired, Exception):
            env = "dev"
    else:
        env = context.resolve_env(env_dev, env_prod)
    product_path = os.path.join(workspace, product)

    all_containers: list[tuple[str, dict]] = []  # (source_label, container)
    docker_dir = os.path.join(product_path, "docker")

    # In prod/deploy: everything runs under one compose project (docker-compose.deploy.yml)
    deploy_compose = os.path.join(docker_dir, "docker-compose.deploy.yml")
    if env == "prod" and os.path.isfile(deploy_compose):
        for c in _get_containers("docker", deploy_compose):
            svc = c.get("Service") or c.get("Name", "?")
            # Label: feature name if it's a feature service, product name otherwise
            if svc.startswith("splent_feature_"):
                label = svc
            else:
                label = product
            all_containers.append((label, c))
    else:
        # Dev: each feature has its own compose project
        pyproject_path = os.path.join(product_path, "pyproject.toml")
        if os.path.exists(pyproject_path):
            with open(pyproject_path, "rb") as f:
                data = tomllib.load(f)
            features = read_features_from_data(data, env)

            for feat in features:
                clean = compose.normalize_feature_ref(feat)
                feat_docker_dir = compose.feature_docker_dir(workspace, clean)
                compose_preferred = os.path.join(
                    feat_docker_dir, f"docker-compose.{env}.yml"
                )
                compose_fallback = os.path.join(feat_docker_dir, "docker-compose.yml")
                compose_file = (
                    compose_preferred
                    if os.path.exists(compose_preferred)
                    else compose_fallback
                )

                if not os.path.exists(compose_file):
                    continue

                proj = compose.project_name(clean, env)
                for c in _get_containers(proj, compose_file):
                    short = clean.split("/")[-1] if "/" in clean else clean
                    all_containers.append((short, c))

        # Product containers
        product_compose = compose.resolve_file(product_path, env)
        if product_compose:
            proj = compose.project_name(product, env)
            for c in _get_containers(proj, product_compose):
                all_containers.append((product, c))

    if not all_containers:
        click.secho(
            f"ℹ️  No containers found for {product} ({env}). Is it up?", fg="yellow"
        )
        return

    click.secho(f"\n{product}  [{env}]\n", bold=True)

    col_source = max(len(src) for src, _ in all_containers) + 2
    col_source = max(col_source, len("SOURCE") + 2)
    col_name = 30
    col_state = 14

    header = f"  {'SOURCE':<{col_source}} {'SERVICE':<{col_name}} {'STATUS':<{col_state}} PORTS"
    click.secho(header, fg="cyan")
    click.echo("  " + "─" * (col_source + col_name + col_state + 20))

    accessible = []

    for source, c in all_containers:
        name = c.get("Service") or c.get("Name", "?")
        state = c.get("State") or c.get("Status", "?")
        ports = _format_ports(c.get("Publishers") or c.get("Ports", ""))

        source_styled = click.style(f"{source:<{col_source}}", fg="bright_black")
        click.echo(
            f"  {source_styled} {name:<{col_name}} {_status_color(state):<{col_state}} {ports}"
        )

        # Collect services by kind
        publishers = c.get("Publishers") or c.get("Ports", [])
        if isinstance(publishers, list):
            for p in publishers:
                pub = p.get("PublishedPort", 0)
                target = p.get("TargetPort", 0)
                if pub and target:
                    result = _service_label(source, name, target)
                    if result:
                        label, kind = result
                        accessible.append((label, pub, kind))

    click.echo()

    if accessible:
        # Deduplicate (Docker reports IPv4 and IPv6 separately)
        seen = set()
        unique = []
        for label, port, kind in accessible:
            key = (label, port)
            if key not in seen:
                seen.add(key)
                unique.append((label, port, kind))

        http_services = [(lbl, p) for lbl, p, k in unique if k == "http"]
        tcp_services = [(lbl, p) for lbl, p, k in unique if k == "tcp"]
        all_services = http_services + tcp_services
        label_width = max((len(lbl) for lbl, _ in all_services), default=20) + 2

        if http_services:
            click.secho("  Accessible services:", bold=True)
            click.echo("  " + "─" * (col_source + col_name + col_state + 20))
            for label, port in http_services:
                url = f"http://localhost:{port}"
                click.echo(f"    {label:<{label_width}} {click.style(url, fg='cyan')}")

        if tcp_services:
            if http_services:
                click.echo()
            click.secho("  Connection endpoints:", bold=True)
            click.echo("  " + "─" * (col_source + col_name + col_state + 20))
            for label, port in tcp_services:
                endpoint = f"localhost:{port}"
                click.echo(
                    f"    {label:<{label_width}} {click.style(endpoint, fg='cyan')}"
                )

        click.echo()

    # ── Port offset summary ──────────────────────────────────────────
    import zlib

    port_offset = zlib.crc32(product.encode("utf-8")) % 1000

    # Collect all unique port mappings (published → target)
    offset_rows = []
    seen_offset = set()
    for source, c in all_containers:
        publishers = c.get("Publishers") or c.get("Ports", [])
        service = c.get("Service") or c.get("Name", "?")
        if isinstance(publishers, list):
            for p in publishers:
                pub = p.get("PublishedPort", 0)
                target = p.get("TargetPort", 0)
                if pub and target and (service, target) not in seen_offset:
                    seen_offset.add((service, target))
                    base = pub - port_offset
                    is_offset = base > 0 and base != pub
                    offset_rows.append(
                        (service, target, base if is_offset else pub, pub, is_offset)
                    )

    if offset_rows:
        click.secho(f"  Port offset: +{port_offset}", bold=True)
        click.style(f'  (crc32("{product}") % 1000 = {port_offset})', dim=True)
        click.echo(
            click.style(f'  crc32("{product}") % 1000 = {port_offset}', dim=True)
        )
        click.echo()

        col_svc = max(len(r[0]) for r in offset_rows) + 2
        col_svc = max(col_svc, len("SERVICE") + 2)
        click.echo(
            click.style(
                f"    {'SERVICE':<{col_svc}} {'BASE':>6}  {'OFFSET':>6}  {'HOST':>6}  CONTAINER",
                dim=True,
            )
        )
        click.echo("    " + "─" * (col_svc + 36))
        for service, target, base, pub, is_offset in offset_rows:
            if is_offset:
                offset_str = click.style(f"+{port_offset}", fg="yellow")
                click.echo(
                    f"    {service:<{col_svc}} {base:>6}  {offset_str:>15}  {pub:>6}  → {target}"
                )
            else:
                click.echo(
                    f"    {service:<{col_svc}} {pub:>6}  {'—':>6}  {pub:>6}  → {target}"
                )
        click.echo()


cli_command = product_docker
