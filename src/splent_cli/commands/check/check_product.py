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
    normalize_namespace,
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
    candidate = os.path.join(product_path, "features", ns_safe, dir_name, "pyproject.toml")
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


def _check_env_vars(workspace, product_path, features, ok_fn, fail_fn, warn_fn):
    """Check that env vars declared in feature contracts are present in the product .env."""
    click.echo(click.style("  Env vars (from feature contracts)", bold=True))

    product_env = _load_product_env(product_path)
    if not product_env:
        warn_fn("No product docker/.env found — run: splent product:env --merge --dev")
        return

    all_required: list[tuple[str, str]] = []  # (var_name, feature_name)

    for entry in features:
        ns_safe, name, version = parse_feature_entry(entry)
        data = _resolve_feature_pyproject(workspace, product_path, ns_safe, name, version)
        if not data:
            continue
        env_vars = (
            data.get("tool", {})
            .get("splent", {})
            .get("contract", {})
            .get("requires", {})
            .get("env_vars", [])
        )
        for var in env_vars:
            all_required.append((var, name))

    if not all_required:
        ok_fn("No env vars required by any feature contract")
        return

    missing = []
    for var, feature in all_required:
        if var not in product_env:
            missing.append((var, feature))

    if missing:
        for var, feature in missing:
            fail_fn(f"{var} missing from docker/.env (required by {feature})"
                    f" — run: splent product:env --merge --dev")
    else:
        ok_fn(f"All {len(all_required)} required env var(s) present in docker/.env")


def _check_symlinks(product_path, features, ok_fn, fail_fn, warn_fn):
    """Check that feature symlinks in the product resolve correctly."""
    click.echo(click.style("  Symlinks", bold=True))

    features_dir = os.path.join(product_path, "features")
    if not os.path.isdir(features_dir):
        warn_fn(f"No features/ directory in product")
        return

    broken = []
    for entry in features:
        ns_safe, name, version = parse_feature_entry(entry)
        dir_name = f"{name}@{version}" if version else name
        link = os.path.join(features_dir, ns_safe, dir_name)

        if os.path.islink(link) and not os.path.exists(link):
            broken.append(f"{ns_safe}/{dir_name}")

    if broken:
        for b in broken:
            fail_fn(f"Broken symlink: {b}")
    else:
        ok_fn(f"All {len(features)} feature symlink(s) OK")


def _check_config_overwrites(ok_fn, fail_fn, warn_fn):
    """Boot the app and check for config overwrites."""
    click.echo(click.style("  Config overwrites", bold=True))

    try:
        from splent_cli.utils.dynamic_imports import get_app
        app = get_app()
    except Exception:
        warn_fn("Could not boot app — skipping config checks")
        return

    trace = app.extensions.get("splent_config_trace", {})
    overwrites = {k: v for k, v in trace.items() if v.get("action") == "overwritten"}

    if overwrites:
        for key, info in sorted(overwrites.items()):
            prev_src = info.get("prev_source", "?")
            source = info.get("source", "?")
            if "." in source:
                source = f"feature ({source.rsplit('.', 1)[1]})"
            warn_fn(f"{key}: {prev_src} overwritten by {source}")
    else:
        ok_fn("No config key overwrites detected")

    # Check features with no blueprints
    click.echo(click.style("  Blueprint registration", bold=True))
    bp_trace = app.extensions.get("splent_blueprint_trace", {})
    feature_sources = set(bp_trace.values())

    config_trace = app.extensions.get("splent_config_trace", {})
    config_sources = {
        v["source"] for v in config_trace.values()
        if "." in v.get("source", "")
    }

    all_feature_sources = feature_sources | config_sources
    features_with_no_bp = config_sources - feature_sources

    if features_with_no_bp:
        for src in sorted(features_with_no_bp):
            name = src.rsplit(".", 1)[-1]
            warn_fn(f"{name} injected config but registered no blueprints")
    else:
        ok_fn(f"{len(feature_sources)} feature(s) registered blueprints")


@click.command(
    "check:product",
    short_help="Validate product health: env vars, symlinks, config.",
)
@context.requires_product
def check_product():
    """Run product-level health checks.

    Validates that the active product is correctly wired:
    env vars from feature contracts are set, symlinks resolve,
    and config overwrites are flagged.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    product_path = os.path.join(workspace, product)

    ok = fail = warn = 0

    def _ok(msg):
        nonlocal ok
        ok += 1
        click.echo(click.style("  [✔] ", fg="green") + msg)

    def _fail(msg):
        nonlocal fail
        fail += 1
        click.echo(click.style("  [✖] ", fg="red") + msg)

    def _warn(msg):
        nonlocal warn
        warn += 1
        click.echo(click.style("  [⚠] ", fg="yellow") + msg)

    click.echo()

    try:
        features = load_product_features(product_path, os.getenv("SPLENT_ENV"))
    except FileNotFoundError:
        _fail("pyproject.toml not found")
        raise SystemExit(1)

    if not features:
        _warn("No features declared")
        click.echo()
        return

    # Check for stale contracts before reading them
    from splent_cli.utils.contract_freshness import check_and_refresh_contracts
    check_and_refresh_contracts(workspace, features)

    _check_env_vars(workspace, product_path, features, _ok, _fail, _warn)
    _check_symlinks(product_path, features, _ok, _fail, _warn)
    _check_config_overwrites(_ok, _fail, _warn)

    # Summary
    click.echo()
    parts = []
    if ok:
        parts.append(click.style(f"{ok} passed", fg="green"))
    if warn:
        parts.append(click.style(f"{warn} warnings", fg="yellow"))
    if fail:
        parts.append(click.style(f"{fail} failed", fg="red"))
    click.echo("  " + ", ".join(parts))

    if fail:
        raise SystemExit(1)


cli_command = check_product
