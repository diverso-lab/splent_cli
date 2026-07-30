"""
check:infra — Validate Docker infrastructure declarations (ports, services, containers, networks).
"""

import os
import re
import subprocess
import json

import click
import yaml

from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.utils.io_utils import load_toml


class _Result:
    """Minimal stand-in for CompletedProcess used by the guarded ``_run``."""

    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run(cmd: list) -> _Result:
    """Run a docker command, never crashing the diagnostic.

    Missing tool (FileNotFoundError) and timeouts are mapped to a non-zero
    returncode so callers treat them as a normal FAIL/skip, exactly like
    check_docker._run.
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return _Result(r.returncode, r.stdout, r.stderr)
    except FileNotFoundError:
        return _Result(1, "", "command not found")
    except subprocess.TimeoutExpired:
        return _Result(1, "", "timed out")


def _config(compose_file: str, env_args: list[str] | None = None) -> dict | None:
    """Return ``docker compose config`` as a dict, or None when unreadable.

    ``env_args`` is the product's ``--env-file``. A feature's compose file
    interpolates variables that live only in the product's merged .env, the
    host port with this product's offset applied and whatever ``__PRODUCT__``
    resolved to, so reading it without that file reports ports and names that
    are not the ones docker will use.
    """
    result = _run(
        [
            "docker",
            "compose",
            *(env_args or []),
            "-f",
            compose_file,
            "config",
            "--format",
            "json",
        ]
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _parse_compose_ports(
    compose_file: str, env_args: list[str] | None = None
) -> list[tuple[int, str, str]]:
    """Return [(host_port, service_name, source_label)] from a compose file."""
    config = _config(compose_file, env_args)
    if config is None:
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


def _raw(compose_file: str) -> dict | None:
    """The compose file as written, before Compose substitutes anything.

    The isolation rules are all about variables, so they can only be checked
    here. ``docker compose config`` resolves ``${X_HOST_PORT}`` to a number,
    which is precisely what a literal port looks like, so the one check that
    matters most is impossible to make against the resolved form.
    """
    try:
        with open(compose_file, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _published(entry) -> str | None:
    """The host side of a compose port entry, as written.

    Long form is a mapping with ``published``. Short form is a string, where
    the host port is the second-to-last colon-separated piece and a bare
    ``"9200"`` publishes nothing on a fixed host port at all. The host may
    itself be an address, so the pieces are counted from the right.

    ``${VAR:-default}`` carries a colon of its own, and splitting through it
    would leave ``-9201}``, which contains no ``${`` and so would be reported
    as a hardcoded port: a false accusation against a file doing exactly the
    right thing. Substitutions are therefore held together while splitting.
    """
    if isinstance(entry, dict):
        published = entry.get("published")
        return None if published is None else str(published)

    text = str(entry)
    parts, current, depth = [], [], 0
    for char in text:
        if char == "{" and current and current[-1] == "$":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == ":" and not depth:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))

    return parts[-2] if len(parts) >= 2 else None


def _isolation_findings(label: str, compose_file: str) -> list[tuple[str, str]]:
    """Check one feature's compose file against the per-product stack rules.

    Returns [(severity, message)] with severity in {"fail", "warn"}. A feature
    that breaks these does not fail on its own; it fails the day a second
    product of the same line runs beside the first, which is the day a product
    line is doing what it exists for.
    """
    raw = _raw(compose_file)
    if raw is None:
        return [("warn", f"{label}: compose file could not be read")]

    findings: list[tuple[str, str]] = []
    services = raw.get("services") or {}
    if not isinstance(services, dict):
        return findings

    declared_volumes = set((raw.get("volumes") or {}) or ())
    env_example = os.path.join(os.path.dirname(compose_file), ".env.example")
    env_keys: set[str] = set()
    if os.path.isfile(env_example):
        with open(env_example, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    env_keys.add(line.split("=", 1)[0].strip())

    referenced_ports: set[str] = set()

    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue

        if svc.get("container_name"):
            findings.append(
                (
                    "fail",
                    f"{label}/{svc_name} sets container_name. Compose does not "
                    f"prefix it, so the second product to start collides with "
                    f"the first. Remove it and let Compose derive the name.",
                )
            )

        for entry in svc.get("ports") or []:
            published = _published(entry)
            if published is None:
                continue
            if "${" not in published:
                findings.append(
                    (
                        "fail",
                        f"{label}/{svc_name} publishes host port {published} "
                        f"literally. Every product would want that one port. "
                        f"Use ${{{label.replace('splent_feature_', '').upper()}"
                        f"_HOST_PORT}} and give it a default in "
                        f"docker/.env.example.",
                    )
                )
                continue
            for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", published):
                referenced_ports.add(name)

        for mount in svc.get("volumes") or []:
            if isinstance(mount, dict):
                source, kind, read_only = (
                    mount.get("source"),
                    mount.get("type"),
                    mount.get("read_only"),
                )
                is_bind = kind == "bind" or (source or "").startswith((".", "/", "$"))
            else:
                pieces = str(mount).split(":")
                source = pieces[0]
                read_only = pieces[-1] == "ro"
                is_bind = source.startswith((".", "/", "$"))
            if is_bind and not read_only:
                findings.append(
                    (
                        "warn",
                        f"{label}/{svc_name} writes to a host path ({source}). "
                        f"Host paths are not prefixed per product, so every "
                        f"product writes into the same directory. Use a named "
                        f"volume, or mount it read-only if it is configuration.",
                    )
                )
            elif not is_bind and source and source not in declared_volumes:
                findings.append(
                    (
                        "warn",
                        f"{label}/{svc_name} mounts '{source}', which is not "
                        f"declared under top-level volumes:.",
                    )
                )

    for name in sorted(referenced_ports):
        if not name.endswith("_HOST_PORT"):
            findings.append(
                (
                    "warn",
                    f"{label}: host port comes from {name}, which does not end "
                    f"in _HOST_PORT, so product:env will not offset it per "
                    f"product and two products will fight over one port.",
                )
            )
        elif env_keys and name not in env_keys:
            findings.append(
                (
                    "fail",
                    f"{label}: {name} has no default in docker/.env.example, so "
                    f"Compose starts with it unset and publishes on a random "
                    f"host port instead of failing.",
                )
            )

    if referenced_ports and not os.path.isfile(env_example):
        findings.append(
            (
                "fail",
                f"{label}: publishes a port through a variable but ships no "
                f"docker/.env.example, so no product has a value for it.",
            )
        )

    networks = raw.get("networks") or {}
    for net_name, net_def in networks.items():
        if not isinstance(net_def, dict) or not net_def.get("external"):
            continue
        if "${" not in str(net_def.get("name", "")):
            findings.append(
                (
                    "fail",
                    f"{label}: network '{net_name}' is pinned to one name, so "
                    f"every product's containers share it and answer to the "
                    f"same DNS aliases. Add: "
                    f"name: ${{SPLENT_NETWORK:-splent_network}}",
                )
            )

    return findings


def _parse_compose_services(
    compose_file: str, env_args: list[str] | None = None
) -> list[tuple[str, str, str]]:
    """Return [(service_name, container_name_or_None, source_label)]."""
    config = _config(compose_file, env_args)
    if config is None:
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

    data = load_toml(pyproject_path, what="pyproject.toml")

    env = os.getenv("SPLENT_ENV", "dev")
    features = read_features_from_data(data, env)

    # Every compose file is read through the product's merged .env, the same one
    # the product's stacks run with, so what this check validates is what docker
    # would actually publish and name.
    env_args = compose.env_file_args(product_path, env)

    # Collect all compose files
    compose_files: list[tuple[str, str]] = []  # (label, path)
    feature_compose_files: list[tuple[str, str]] = []  # features only

    for feat in features:
        clean = compose.normalize_feature_ref(feat)
        # The full ref, namespace included, is what feature_docker_dir expects
        # and what every other command passes it. Handing it the bare name
        # dropped the namespace from the cache path, so a feature installed
        # from cache resolved to a directory that does not exist, resolve_file
        # answered None, and the feature silently vanished from every check
        # below. The report then said "[OK] No port conflicts" about ports it
        # had never read.
        feat_base = os.path.dirname(compose.feature_docker_dir(workspace, clean))
        cf = compose.resolve_file(feat_base, env)
        if cf:
            label = clean.split("/")[-1] if "/" in clean else clean
            compose_files.append((label, cf))
            feature_compose_files.append((label, cf))

    cf = compose.resolve_file(product_path, env)
    if cf:
        compose_files.append((product, cf))

    # --- Check 1: Port conflicts between declarations ---
    click.echo(click.style("  Ports", bold=True))
    all_ports: dict[int, list[str]] = {}  # port -> [labels]
    for label, cf in compose_files:
        for host_port, svc_name, _ in _parse_compose_ports(cf, env_args):
            all_ports.setdefault(host_port, []).append(f"{label}/{svc_name}")

    port_conflicts = {p: srcs for p, srcs in all_ports.items() if len(srcs) > 1}
    if port_conflicts:
        for port, sources in sorted(port_conflicts.items()):
            _fail(f"Port {port} declared by multiple services: {', '.join(sources)}")
    else:
        _ok(f"No port conflicts ({len(all_ports)} ports declared)")

    # Check against running containers (query docker ps once, not per-port)
    running_conflicts = []
    ps_result = _run(["docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Ports}}"])
    if ps_result.returncode == 0:
        ps_lines = ps_result.stdout.splitlines()
        for port in all_ports:
            for line in ps_lines:
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
        for svc_name, container_name, _ in _parse_compose_services(cf, env_args):
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

    # --- Check 3: One stack per product ---
    #
    # A feature that contributes containers gets one instance per product. The
    # naming is the CLI's job and is already done; what a feature's compose
    # file has to hold up its end of is checked here. None of it fails on a
    # single product, which is why it needs a check: it fails the first time
    # two products of one line run together, and by then the symptom is a
    # random port or an intermittently wrong answer rather than an error.
    click.echo()
    click.echo(click.style("  Isolation", bold=True))
    if feature_compose_files:
        isolation: list[tuple[str, str]] = []
        for label, cf in feature_compose_files:
            isolation.extend(_isolation_findings(label, cf))
        for severity, message in isolation:
            (_fail if severity == "fail" else _warn)(message)
        if not isolation:
            _ok(f"{len(feature_compose_files)} feature stack(s) isolate per product")
    else:
        _ok("No feature contributes containers")

    # --- Check 4: Network availability ---
    click.echo()
    click.echo(click.style("  Networks", bold=True))
    required_networks: set[str] = set()
    for label, cf in compose_files:
        config = _config(cf, env_args)
        if config is None:
            continue
        for net_name, net_def in config.get("networks", {}).items():
            if isinstance(net_def, dict) and net_def.get("external"):
                # The key is what compose files reference; 'name' is the
                # network that has to exist on the host, and since it is
                # derived per product the two are no longer the same string.
                required_networks.add(net_def.get("name") or net_name)

    if required_networks:
        existing_networks = _run(
            ["docker", "network", "ls", "--format", "{{.Name}}"]
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
        config = _config(cf, env_args)
        if config is None:
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
        config = _config(cf, env_args)
        if config is None:
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
        config = _config(cf, env_args)
        if config is None:
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
