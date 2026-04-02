"""
check:infra — Validate Docker infrastructure declarations (ports, services, containers, networks).
"""

import os
import subprocess
import json

import click
import tomllib

from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data


def _parse_compose_ports(compose_file: str) -> list[tuple[int, str, str]]:
    """Return [(host_port, service_name, source_label)] from a compose file."""
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
                    ports.append((int(published), svc_name, compose_file))
                except (ValueError, TypeError):
                    pass
    return ports


def _parse_compose_services(compose_file: str) -> list[tuple[str, str, str]]:
    """Return [(service_name, container_name_or_None, source_label)]."""
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

    services = []
    for svc_name, svc in config.get("services", {}).items():
        container_name = svc.get("container_name")
        services.append((svc_name, container_name, compose_file))
    return services


@click.command(
    "check:infra",
    short_help="Validate Docker infrastructure (ports, services, networks).",
)
def check_infra():
    """Check for port conflicts, duplicate services, container name collisions,
    and network availability across all features and the product."""
    workspace = str(context.workspace())
    product = context.require_app()
    product_path = os.path.join(workspace, product)
    pyproject_path = os.path.join(product_path, "pyproject.toml")

    ok = fail = warn = 0

    def _ok(msg):
        nonlocal ok
        ok += 1
        click.echo(click.style("  [OK] ", fg="green") + msg)

    def _fail(msg):
        nonlocal fail
        fail += 1
        click.echo(click.style("  [FAIL] ", fg="red") + msg)

    def _warn(msg):
        nonlocal warn
        warn += 1
        click.echo(click.style("  [WARN] ", fg="yellow") + msg)

    click.echo()
    click.echo(click.style("  Infrastructure check", bold=True))
    click.echo()

    if not os.path.exists(pyproject_path):
        _fail("pyproject.toml not found")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    env = os.getenv("SPLENT_ENV", "dev")
    features = read_features_from_data(data, env)

    # Collect all compose files
    compose_files: list[tuple[str, str]] = []  # (label, path)

    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        bare_name = clean.split("/")[-1] if "/" in clean else clean
        feat_base = os.path.dirname(compose.feature_docker_dir(workspace, bare_name))
        cf = compose.resolve_file(feat_base, env)
        if cf:
            compose_files.append((bare_name, cf))

    cf = compose.resolve_file(product_path, env)
    if cf:
        compose_files.append((product, cf))

    # --- Check 1: Port conflicts between declarations ---
    click.echo(click.style("  Ports", bold=True))
    all_ports: dict[int, list[str]] = {}  # port -> [labels]
    for label, cf in compose_files:
        for host_port, svc_name, _ in _parse_compose_ports(cf):
            all_ports.setdefault(host_port, []).append(f"{label}/{svc_name}")

    port_conflicts = {p: srcs for p, srcs in all_ports.items() if len(srcs) > 1}
    if port_conflicts:
        for port, sources in sorted(port_conflicts.items()):
            _fail(f"Port {port} declared by multiple services: {', '.join(sources)}")
    else:
        _ok(f"No port conflicts ({len(all_ports)} ports declared)")

    # Check against running containers
    running_conflicts = []
    for port in all_ports:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Ports}}"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            cid, name, ports_str = parts
            if f":{port}->" in ports_str:
                running_conflicts.append((port, name))

    if running_conflicts:
        for port, cname in running_conflicts:
            _warn(f"Port {port} already in use by running container: {cname}")
    else:
        _ok("No conflicts with running containers")

    # --- Check 2: Service name collisions ---
    click.echo()
    click.echo(click.style("  Services", bold=True))
    all_services: dict[str, list[str]] = {}  # svc_name -> [labels]
    all_container_names: dict[str, list[str]] = {}  # container_name -> [labels]

    for label, cf in compose_files:
        for svc_name, container_name, _ in _parse_compose_services(cf):
            all_services.setdefault(svc_name, []).append(label)
            if container_name:
                all_container_names.setdefault(container_name, []).append(label)

    svc_conflicts = {s: srcs for s, srcs in all_services.items() if len(srcs) > 1}
    if svc_conflicts:
        for svc, sources in sorted(svc_conflicts.items()):
            _warn(f"Service '{svc}' defined by multiple features: {', '.join(sources)}")
    else:
        _ok(f"No service name collisions ({len(all_services)} services)")

    cn_conflicts = {c: srcs for c, srcs in all_container_names.items() if len(srcs) > 1}
    if cn_conflicts:
        for cn, sources in sorted(cn_conflicts.items()):
            _fail(
                f"Container name '{cn}' used by multiple features: {', '.join(sources)}"
            )
    else:
        _ok(
            f"No container name collisions ({len(all_container_names)} named containers)"
        )

    # --- Check 3: Network availability ---
    click.echo()
    click.echo(click.style("  Networks", bold=True))
    required_networks: set[str] = set()
    for label, cf in compose_files:
        result = subprocess.run(
            ["docker", "compose", "-f", cf, "config", "--format", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        try:
            config = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        for net_name, net_def in config.get("networks", {}).items():
            if isinstance(net_def, dict) and net_def.get("external"):
                required_networks.add(net_name)

    if required_networks:
        existing_networks = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for net in sorted(required_networks):
            if net in existing_networks:
                _ok(f"Network '{net}' exists")
            else:
                _fail(
                    f"External network '{net}' does not exist (run: docker network create {net})"
                )
    else:
        _ok("No external networks required")

    # --- Check 4: Dockerfile build contexts ---
    click.echo()
    click.echo(click.style("  Build contexts", bold=True))
    build_count = 0
    for label, cf in compose_files:
        docker_dir = os.path.dirname(cf)
        result = subprocess.run(
            ["docker", "compose", "-f", cf, "config", "--format", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        try:
            config = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        for svc_name, svc_def in config.get("services", {}).items():
            build_cfg = svc_def.get("build")
            if not build_cfg:
                continue
            build_count += 1
            if isinstance(build_cfg, dict):
                ctx = build_cfg.get("context", ".")
                dockerfile = build_cfg.get("dockerfile", "Dockerfile")
                df_path = (
                    os.path.join(ctx, dockerfile)
                    if os.path.isabs(ctx)
                    else os.path.join(docker_dir, ctx, dockerfile)
                )
            else:
                df_path = os.path.join(docker_dir, build_cfg, "Dockerfile")

            if os.path.isfile(df_path):
                _ok(f"{label}/{svc_name}: Dockerfile found")
            else:
                _fail(f"{label}/{svc_name}: Dockerfile not found at {df_path}")

    if build_count == 0:
        _ok("No custom builds (all services use pre-built images)")

    # --- Check 5: Healthcheck coverage ---
    click.echo()
    click.echo(click.style("  Health checks", bold=True))
    services_with_hc: set[str] = set()
    services_depended_on: dict[str, str] = {}  # depended_svc -> by_svc

    for label, cf in compose_files:
        result = subprocess.run(
            ["docker", "compose", "-f", cf, "config", "--format", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        try:
            config = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        for svc_name, svc_def in config.get("services", {}).items():
            if "healthcheck" in svc_def:
                services_with_hc.add(svc_name)
            for dep_svc, dep_cfg in (svc_def.get("depends_on") or {}).items():
                if (
                    isinstance(dep_cfg, dict)
                    and dep_cfg.get("condition") == "service_healthy"
                ):
                    services_depended_on[dep_svc] = svc_name

    missing_hc = {
        svc: by
        for svc, by in services_depended_on.items()
        if svc not in services_with_hc
    }
    if missing_hc:
        for svc, by in sorted(missing_hc.items()):
            _fail(
                f"'{by}' depends on '{svc}' being healthy, but '{svc}' has no healthcheck"
            )
    elif services_with_hc:
        _ok(f"{len(services_with_hc)} service(s) with health checks")
    else:
        _ok("No health checks declared (none required)")

    # --- Check 6: Volume name conflicts ---
    click.echo()
    click.echo(click.style("  Volumes", bold=True))
    all_volumes: dict[str, list[str]] = {}  # vol_name -> [labels]
    for label, cf in compose_files:
        result = subprocess.run(
            ["docker", "compose", "-f", cf, "config", "--format", "json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            continue
        try:
            config = json.loads(result.stdout)
        except json.JSONDecodeError:
            continue
        for vol_name in config.get("volumes", {}):
            all_volumes.setdefault(vol_name, []).append(label)

    vol_conflicts = {v: srcs for v, srcs in all_volumes.items() if len(srcs) > 1}
    if vol_conflicts:
        for vol, sources in sorted(vol_conflicts.items()):
            _warn(f"Volume '{vol}' declared by multiple features: {', '.join(sources)}")
    else:
        _ok(f"No volume name conflicts ({len(all_volumes)} volumes)")

    # --- Summary ---
    click.echo()
    if fail:
        click.secho(
            f"  {fail} check(s) failed, {warn} warning(s), {ok} passed.", fg="red"
        )
        raise SystemExit(1)
    elif warn:
        click.secho(
            f"  All passed with {warn} warning(s) ({ok} checks OK).", fg="yellow"
        )
    else:
        click.secho(f"  All {ok} checks passed.", fg="green")
    click.echo()


cli_command = check_infra
