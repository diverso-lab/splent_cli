"""
check:product — Product-level health checks.

Validates that the active product is correctly wired:
  1. Required env vars from feature contracts are set
  2. Feature symlinks resolve to real directories
  3. Config overwrites are flagged
  4. Features that loaded but registered no blueprints are warned
"""

import os
import tomllib

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import (
    load_product_features,
    parse_feature_entry,
)


def _resolve_feature_pyproject(workspace, product_path, ns_safe, name, version):
    """Find and load a feature's pyproject.toml. Returns dict or None."""
    # 1. Editable at workspace root
    candidate = os.path.join(workspace, name, "pyproject.toml")
    if os.path.isfile(candidate):
        with open(candidate, "rb") as f:
            return tomllib.load(f)

    # 2. Product symlink
    dir_name = f"{name}@{version}" if version else name
    candidate = os.path.join(
        product_path, "features", ns_safe, dir_name, "pyproject.toml"
    )
    if os.path.isfile(candidate):
        with open(candidate, "rb") as f:
            return tomllib.load(f)

    # 3. Cache
    candidate = os.path.join(
        workspace, ".splent_cache", "features", ns_safe, dir_name, "pyproject.toml"
    )
    if os.path.isfile(candidate):
        with open(candidate, "rb") as f:
            return tomllib.load(f)

    return None


def _load_product_env(product_path: str) -> dict[str, str]:
    """Load the product's docker/.env into a dict."""
    env_file = os.path.join(product_path, "docker", ".env")
    env_vars = {}
    if not os.path.isfile(env_file):
        return env_vars
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
    return env_vars


def _short(name: str) -> str:
    return name.removeprefix("splent_feature_")


# ── Env vars ─────────────────────────────────────────────────────────────────


def _check_env_vars(workspace, product_path, features, counters):
    """Check required env vars from feature contracts."""
    click.echo()
    click.secho("  Env Vars", bold=True, fg="cyan")
    click.echo()

    product_env = _load_product_env(product_path)
    if not product_env:
        click.echo(click.style("  ⚠  No docker/.env found", fg="yellow"))
        counters["warn"] += 1
        return

    # Collect required vars per feature
    per_feature: dict[str, list[str]] = {}
    for entry in features:
        ns_safe, name, version = parse_feature_entry(entry)
        data = _resolve_feature_pyproject(
            workspace, product_path, ns_safe, name, version
        )
        if not data:
            continue
        env_vars = (
            data.get("tool", {})
            .get("splent", {})
            .get("contract", {})
            .get("requires", {})
            .get("env_vars", [])
        )
        if env_vars:
            per_feature[name] = env_vars

    if not per_feature:
        click.echo("  No env vars required by any feature contract.")
        return

    # Get injected keys from config trace
    injected_keys: set[str] = set()
    try:
        from splent_cli.utils.dynamic_imports import get_app

        app = get_app()
        trace = app.extensions.get("splent_config_trace", {})
        injected_keys = set(trace.keys())
    except Exception:
        pass

    # Group by variable: var → list of feature short names
    var_features: dict[str, list[str]] = {}
    for name, vars_list in per_feature.items():
        for var in vars_list:
            var_features.setdefault(var, []).append(_short(name))

    # Table
    col_var = 30
    col_feat = 25
    col_src = 14

    click.echo(
        f"  {'Variable':<{col_var}}  {'Feature(s)':<{col_feat}}  {'Source':<{col_src}}  Status"
    )
    click.echo(f"  {'-' * col_var}  {'-' * col_feat}  {'-' * col_src}  {'-' * 10}")

    has_missing = False
    for var in sorted(var_features):
        feats = ", ".join(sorted(var_features[var]))

        if var in product_env:
            src = "docker/.env"
            status = click.style("✔", fg="green")
        elif var in injected_keys:
            src = "inject_config"
            status = click.style("✔", fg="green")
        else:
            src = "—"
            status = click.style("✖ missing", fg="red")
            has_missing = True
            counters["fail"] += 1

        click.echo(
            f"  {var:<{col_var}}  {feats:<{col_feat}}  {src:<{col_src}}  {status}"
        )

    if not has_missing:
        counters["ok"] += 1


# ── Symlinks ─────────────────────────────────────────────────────────────────


def _check_symlinks(product_path, features, counters):
    """Check that feature symlinks resolve correctly."""
    click.echo()
    click.secho("  Symlinks", bold=True, fg="cyan")
    click.echo()

    features_dir = os.path.join(product_path, "features")
    if not os.path.isdir(features_dir):
        click.echo(click.style("  ⚠  No features/ directory", fg="yellow"))
        counters["warn"] += 1
        return

    col_feat = 25
    click.echo(f"  {'Feature':<{col_feat}}  Status")
    click.echo(f"  {'-' * col_feat}  {'-' * 10}")

    broken_count = 0
    for entry in features:
        ns_safe, name, version = parse_feature_entry(entry)
        dir_name = f"{name}@{version}" if version else name
        link = os.path.join(features_dir, ns_safe, dir_name)

        feat_label = _short(name)
        if version:
            feat_label += f"@{version}"

        if os.path.islink(link) and not os.path.exists(link):
            click.echo(
                f"  {feat_label:<{col_feat}}  {click.style('✖ broken', fg='red')}"
            )
            broken_count += 1
        elif os.path.exists(link):
            click.echo(f"  {feat_label:<{col_feat}}  {click.style('✔', fg='green')}")
        else:
            click.echo(
                f"  {feat_label:<{col_feat}}  {click.style('— not linked', fg='bright_black')}"
            )

    if broken_count:
        counters["fail"] += broken_count
    else:
        counters["ok"] += 1


# ── Config overwrites ────────────────────────────────────────────────────────


def _check_config_overwrites(counters):
    """Check for config overwrites between features."""
    click.echo()
    click.secho("  Config Overwrites", bold=True, fg="cyan")
    click.echo()

    try:
        from splent_cli.utils.dynamic_imports import get_app

        app = get_app()
    except Exception:
        click.echo(click.style("  ⚠  Could not boot app — skipping", fg="yellow"))
        counters["warn"] += 1
        return

    trace = app.extensions.get("splent_config_trace", {})
    overwrites = {k: v for k, v in trace.items() if v.get("action") == "overwritten"}

    if not overwrites:
        click.echo("  No config key overwrites detected.")
        counters["ok"] += 1
    else:
        col_key = 25
        col_orig = 25

        click.echo(f"  {'Key':<{col_key}}  {'Set by':<{col_orig}}  Overwritten by")
        click.echo(f"  {'-' * col_key}  {'-' * col_orig}  {'-' * 25}")

        for key, info in sorted(overwrites.items()):
            prev_src = info.get("prev_source", "?")
            source = info.get("source", "?")
            if "." in prev_src:
                prev_src = _short(prev_src.rsplit(".", 1)[1])
            if "." in source:
                source = _short(source.rsplit(".", 1)[1])
            click.echo(
                f"  {key:<{col_key}}  {prev_src:<{col_orig}}  "
                f"{click.style(source, fg='yellow')}"
            )

        counters["warn"] += len(overwrites)


# ── Blueprints ───────────────────────────────────────────────────────────────


def _check_blueprints(counters):
    """Check blueprint registration."""
    click.echo()
    click.secho("  Blueprints", bold=True, fg="cyan")
    click.echo()

    try:
        from splent_cli.utils.dynamic_imports import get_app

        app = get_app()
    except Exception:
        click.echo(click.style("  ⚠  Could not boot app — skipping", fg="yellow"))
        counters["warn"] += 1
        return

    bp_trace = app.extensions.get("splent_blueprint_trace", {})

    if not bp_trace:
        click.echo("  No blueprints registered.")
        return

    col_bp = 20
    col_feat = 25

    click.echo(f"  {'Blueprint':<{col_bp}}  Feature")
    click.echo(f"  {'-' * col_bp}  {'-' * col_feat}")

    for bp_name, source in sorted(bp_trace.items()):
        feat = _short(source.rsplit(".", 1)[-1]) if "." in source else source
        click.echo(f"  {bp_name:<{col_bp}}  {feat}")

    counters["ok"] += 1


# ── Command ──────────────────────────────────────────────────────────────────


@click.command(
    "check:product",
    short_help="Validate product health: env vars, symlinks, config.",
)
@context.requires_product
def check_product():
    """Run product-level health checks."""
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)

    counters = {"ok": 0, "fail": 0, "warn": 0}

    click.echo()
    click.secho(f"  check:product — {product}", bold=True, fg="cyan")

    try:
        features = load_product_features(product_path, os.getenv("SPLENT_ENV"))
    except FileNotFoundError:
        click.secho("  pyproject.toml not found", fg="red")
        raise SystemExit(1)

    if not features:
        click.secho("  No features declared.", fg="yellow")
        click.echo()
        return

    # Check for stale contracts before reading them
    from splent_cli.utils.contract_freshness import check_and_refresh_contracts

    check_and_refresh_contracts(workspace, features)

    _check_env_vars(workspace, product_path, features, counters)
    _check_symlinks(product_path, features, counters)
    _check_config_overwrites(counters)
    _check_blueprints(counters)

    # Summary
    click.echo()
    parts = []
    if counters["ok"]:
        parts.append(click.style(f"{counters['ok']} passed", fg="green"))
    if counters["warn"]:
        parts.append(click.style(f"{counters['warn']} warnings", fg="yellow"))
    if counters["fail"]:
        parts.append(click.style(f"{counters['fail']} failed", fg="red"))
    click.echo("  " + ", ".join(parts))
    click.echo()

    if counters["fail"]:
        raise SystemExit(1)


cli_command = check_product
