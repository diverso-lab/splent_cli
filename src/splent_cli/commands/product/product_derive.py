import json
import os
import subprocess
import tomllib

import click

from splent_cli.services import context, compose
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.commands.uvl.uvl_check import run_uvl_check
from splent_cli.commands.feature.feature_diff import run_all_product_check
from splent_cli.commands.product.product_sync import product_sync
from splent_cli.commands.product.product_env import product_env
from splent_cli.commands.product.product_up import product_up
from splent_cli.commands.product.product_run import product_runc
from splent_cli.commands.product.product_port import product_port


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


def _run_port_check(workspace: str, product: str, product_path: str, features: list, env: str) -> list[dict]:
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
                conflicts.append({
                    "port": host_port,
                    "service": f"{label}/{svc_name}",
                    "containers": blocking,
                })

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
    Runs three pre-flight checks before the pipeline:
      1. uvl:check       — feature selection must be satisfiable under the UVL model.
      2. feature:diff    — no ERROR-level conflicts between feature contracts.
      3. port conflicts  — no running containers occupying required host ports.

    \b
    --dev runs (after pre-flight):
      1. product:sync
      2. product:env --generate --all --dev
      3. product:env --merge --dev
      4. product:up --dev
      5. product:run --dev
      6. product:port

    --prod is not yet available.
    """
    if not mode:
        click.echo("❌ You must specify --dev or --prod.")
        raise SystemExit(1)

    if mode == "prod":
        click.echo(
            click.style("🚧  --prod derivation is not yet available.", fg="yellow")
        )
        raise SystemExit(0)

    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    click.echo(click.style("\n🧬 SPL Product Derivation — dev\n", fg="cyan", bold=True))

    # ── Pre-flight checks ──────────────────────────────────────────────────
    click.echo(click.style("━━ Pre-flight checks", fg="bright_black", bold=True))
    click.echo()

    preflight_failed = False

    # [pre 1/3] uvl:check
    click.echo(click.style("  [1/3] uvl:check", fg="bright_black"))
    uvl_ok, uvl_msg = run_uvl_check(workspace)
    if uvl_ok:
        click.secho("        ✅ UVL configuration is satisfiable.", fg="green")
    else:
        click.secho(f"        🚨 {uvl_msg}", fg="red")
        click.secho("        → Run: splent uvl:check", fg="yellow")
        preflight_failed = True
    click.echo()

    # [pre 2/3] feature:diff --all
    click.echo(click.style("  [2/3] feature:diff --all", fg="bright_black"))
    findings = run_all_product_check(workspace, product_dir)
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]

    if not errors:
        if warnings:
            click.secho(
                f"        ✅ No conflicts. {len(warnings)} warning(s) — "
                "run 'splent feature:diff --all' to review.",
                fg="green",
            )
        else:
            click.secho("        ✅ No conflicts detected.", fg="green")
    else:
        for err in errors:
            click.secho(f"        🚨 [{err['field']}] {err['message']}", fg="red")
        click.secho("        → Run: splent feature:diff --all", fg="yellow")
        preflight_failed = True
    click.echo()

    # [pre 3/3] port conflict check
    click.echo(click.style("  [3/3] port conflicts", fg="bright_black"))
    pyproject_path = os.path.join(product_dir, "pyproject.toml")
    features = []
    if os.path.exists(pyproject_path):
        with open(pyproject_path, "rb") as f:
            features = read_features_from_data(tomllib.load(f), mode)

    port_conflicts = _run_port_check(workspace, product, product_dir, features, mode)

    if not port_conflicts:
        click.secho("        ✅ No port conflicts detected.", fg="green")
    else:
        click.secho(
            f"        ⚠️  {len(port_conflicts)} port conflict(s) found:\n", fg="yellow"
        )
        all_containers: dict[str, str] = {}  # id → name
        for conflict in port_conflicts:
            for cid, cname in conflict["containers"]:
                all_containers[cid] = cname
            container_list = ", ".join(n for _, n in conflict["containers"])
            click.secho(
                f"          port {conflict['port']:>5}  ←  {conflict['service']}"
                f"  (blocked by: {container_list})",
                fg="yellow",
            )

        click.echo()
        stop_them = click.confirm(
            "        Stop and remove the conflicting containers?", default=False
        )
        if stop_them:
            for cid, cname in all_containers.items():
                click.echo(f"        🛑 Stopping {cname}...")
                subprocess.run(["docker", "stop", cid], capture_output=True)
                subprocess.run(["docker", "rm", cid], capture_output=True)
            click.secho("        ✅ Conflicting containers removed.", fg="green")
        else:
            click.secho(
                "        ❌ Cannot proceed with port conflicts unresolved.",
                fg="red",
            )
            preflight_failed = True
    click.echo()

    if preflight_failed:
        click.secho(
            "❌ Pre-flight checks failed. Fix the issues above before deriving the product.",
            fg="red",
            bold=True,
        )
        click.echo()
        raise SystemExit(1)

    click.echo(click.style(f"  {'─' * 70}\n", fg="bright_black"))

    # ── Derivation pipeline ────────────────────────────────────────────────
    ctx = click.get_current_context()

    click.echo(click.style("━━ [1/6] product:sync", fg="bright_black"))
    ctx.invoke(product_sync, force=False)

    click.echo(
        click.style("\n━━ [2/6] product:env --generate --all --dev", fg="bright_black")
    )
    ctx.invoke(
        product_env, generate=True, merge=False, env_name="dev", process_all=True
    )

    click.echo(click.style("\n━━ [3/6] product:env --merge --dev", fg="bright_black"))
    ctx.invoke(
        product_env, generate=False, merge=True, env_name="dev", process_all=False
    )

    click.echo(click.style("\n━━ [4/6] product:up --dev", fg="bright_black"))
    ctx.invoke(product_up, dev=True, prod=False)

    click.echo(click.style("\n━━ [5/6] product:run --dev", fg="bright_black"))
    ctx.invoke(product_runc, env_dev=True, env_prod=False)

    click.echo(click.style("\n━━ [6/6] product:port", fg="bright_black"))
    ctx.invoke(product_port, env_flag="dev")

    click.echo(click.style("\n✅ Product derived successfully.", fg="green", bold=True))
