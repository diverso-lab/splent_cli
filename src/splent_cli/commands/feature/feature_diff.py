"""
splent feature:diff

Compare feature contracts and report potential conflicts.

  Two-feature mode (default):
    splent feature:diff splent_feature_auth splent_feature_profile

  All-product mode:
    splent feature:diff --all

Conflict categories:
  🚨 ERROR   — will break Flask at startup (route or blueprint name collision)
  ⚠️  WARNING — may cause unexpected behaviour (model/service collision, missing dep)
  ℹ️  INFO    — informational (shared hook slots, shared env vars)
"""

import json
import os
import tomllib
import click
from collections import defaultdict
from pathlib import Path

from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace
from splent_framework.utils.pyproject_reader import PyprojectReader


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")

SEVERITY_ICON = {
    "error": click.style("🚨 ERROR  ", fg="red", bold=True),
    "warning": click.style("⚠️  WARNING", fg="yellow", bold=True),
    "info": click.style("ℹ️  INFO   ", fg="cyan"),
}

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


# ─────────────────────────────────────────────────────────────────────────────
# Resolution helpers
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_feature(
    feature_ref: str, workspace: str
) -> tuple[Path, str, str, str | None]:
    """Resolve a feature_ref to (cache_path, ns, name, version)."""
    base, _, version = feature_ref.partition("@")
    version = version or None

    if "/" in base:
        ns_raw, name = base.split("/", 1)
        ns = normalize_namespace(ns_raw)
    else:
        ns = DEFAULT_NAMESPACE
        name = base

    cache_root = Path(workspace) / ".splent_cache" / "features" / ns

    if version:
        candidate = cache_root / f"{name}@{version}"
        if candidate.exists():
            return candidate, ns, name, version
        raise SystemExit(f"❌ Not found in cache: {candidate}")

    candidate = cache_root / name
    if candidate.exists():
        return candidate, ns, name, None

    raise SystemExit(f"❌ Not found in cache: {candidate}")


def _parse_pyproject_entry(entry: str) -> tuple[str, str, str | None]:
    """Return (namespace, name, version | None) from a pyproject feature entry."""
    base, _, version = entry.partition("@")
    if "/" in base:
        ns_raw, name = base.split("/", 1)
        ns = normalize_namespace(ns_raw)
    else:
        ns = DEFAULT_NAMESPACE
        name = base
    return ns, name, version or None


def _resolve_all_product_features(
    product_dir: str, workspace: str
) -> list[tuple[str, Path]]:
    """
    Read all features from the product's pyproject.toml and resolve each to
    (label, cache_path). Skips any entry not found in cache with a warning.
    """
    try:
        features_raw = PyprojectReader.for_product(product_dir).features
    except FileNotFoundError:
        raise SystemExit("❌ pyproject.toml not found in product.")

    if not features_raw:
        raise SystemExit("  No features declared in pyproject.toml.")

    resolved = []
    for entry in features_raw:
        ns, name, version = _parse_pyproject_entry(entry)
        label = f"{name}@{version}" if version else name
        try:
            cache_path, _, _, _ = _resolve_feature(
                f"{ns}/{name}@{version}" if version else f"{ns}/{name}", workspace
            )
            resolved.append((label, cache_path))
        except SystemExit:
            click.secho(f"  ⚠️  {label} not found in cache — skipped.", fg="yellow")

    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# Contract reader
# ─────────────────────────────────────────────────────────────────────────────


def _read_contract(cache_path: Path) -> dict:
    """Read [tool.splent.contract] from a feature's pyproject.toml."""
    pyproject = cache_path / "pyproject.toml"
    if not pyproject.exists():
        return {"description": "", "provides": {}, "requires": {}}

    with open(pyproject, "rb") as f:
        data = tomllib.load(f)

    raw = data.get("tool", {}).get("splent", {}).get("contract", {})
    return {
        "description": raw.get("description", ""),
        "provides": {
            "routes": raw.get("provides", {}).get("routes", []),
            "blueprints": raw.get("provides", {}).get("blueprints", []),
            "models": raw.get("provides", {}).get("models", []),
            "commands": raw.get("provides", {}).get("commands", []),
            "hooks": raw.get("provides", {}).get("hooks", []),
            "services": raw.get("provides", {}).get("services", []),
            "docker": raw.get("provides", {}).get("docker", []),
        },
        "requires": {
            "features": raw.get("requires", {}).get("features", []),
            "env_vars": raw.get("requires", {}).get("env_vars", []),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Analysis: pair mode
# ─────────────────────────────────────────────────────────────────────────────


def _overlap(a: list, b: list) -> list:
    return sorted(set(a) & set(b))


def _analyse_pair(
    contract_a: dict, contract_b: dict, label_a: str, label_b: str
) -> list[dict]:
    """Compare two feature contracts and return findings."""
    findings = []
    prov_a = contract_a["provides"]
    prov_b = contract_b["provides"]
    req_a = contract_a["requires"]
    req_b = contract_b["requires"]

    for route in _overlap(prov_a["routes"], prov_b["routes"]):
        findings.append(
            {
                "severity": "error",
                "field": "routes",
                "values": [route],
                "features": [label_a, label_b],
                "message": f"Route '{route}' registered by both. Flask will raise an AssertionError at startup.",
            }
        )

    for bp in _overlap(prov_a["blueprints"], prov_b["blueprints"]):
        findings.append(
            {
                "severity": "error",
                "field": "blueprints",
                "values": [bp],
                "features": [label_a, label_b],
                "message": f"Blueprint '{bp}' declared by both. The second registration will conflict.",
            }
        )

    for model in _overlap(prov_a["models"], prov_b["models"]):
        findings.append(
            {
                "severity": "warning",
                "field": "models",
                "values": [model],
                "features": [label_a, label_b],
                "message": (
                    f"Model class '{model}' defined by both. "
                    f"Verify that __tablename__ values differ to avoid migration conflicts."
                ),
            }
        )

    for svc in _overlap(prov_a.get("services", []), prov_b.get("services", [])):
        findings.append(
            {
                "severity": "warning",
                "field": "services",
                "values": [svc],
                "features": [label_a, label_b],
                "message": f"Service class '{svc}' defined by both. May cause import ambiguity.",
            }
        )

    for slot in _overlap(prov_a.get("hooks", []), prov_b.get("hooks", [])):
        findings.append(
            {
                "severity": "info",
                "field": "hooks",
                "values": [slot],
                "features": [label_a, label_b],
                "message": (
                    f"Hook slot '{slot}' has callbacks in both features. "
                    f"Both will run — verify the combined output in your layouts."
                ),
            }
        )

    for var in _overlap(req_a["env_vars"], req_b["env_vars"]):
        findings.append(
            {
                "severity": "info",
                "field": "env_vars",
                "values": [var],
                "features": [label_a, label_b],
                "message": f"Env var '{var}' required by both. One .env entry serves both.",
            }
        )

    short_b = label_b.split("splent_feature_", 1)[-1].split("@")[0]
    short_a = label_a.split("splent_feature_", 1)[-1].split("@")[0]

    if short_b in req_a["features"]:
        findings.append(
            {
                "severity": "info",
                "field": "requires",
                "values": [short_b],
                "features": [label_a],
                "message": f"{label_a} declares a dependency on {label_b}.",
            }
        )
    if short_a in req_b["features"]:
        findings.append(
            {
                "severity": "info",
                "field": "requires",
                "values": [short_a],
                "features": [label_b],
                "message": f"{label_b} declares a dependency on {label_a}.",
            }
        )

    return findings


# ─────────────────────────────────────────────────────────────────────────────
# Analysis: all-product mode
# ─────────────────────────────────────────────────────────────────────────────


def _analyse_all(labeled_contracts: list[tuple[str, dict]]) -> list[dict]:
    """
    Check all N features at once for conflicts.

    Builds inverted indexes (value → [features that provide it]) and reports
    any value shared by 2+ features. Cross-dependencies that are NOT satisfied
    by the product are reported as warnings.
    """
    findings = []

    # Inverted indexes: value → list of feature labels
    routes_idx = defaultdict(list)
    blueprints_idx = defaultdict(list)
    models_idx = defaultdict(list)
    services_idx = defaultdict(list)
    hooks_idx = defaultdict(list)
    env_vars_idx = defaultdict(list)

    feature_requires = {}  # label → list of required short names

    for label, contract in labeled_contracts:
        prov = contract["provides"]
        req = contract["requires"]
        for r in prov.get("routes", []):
            routes_idx[r].append(label)
        for b in prov.get("blueprints", []):
            blueprints_idx[b].append(label)
        for m in prov.get("models", []):
            models_idx[m].append(label)
        for s in prov.get("services", []):
            services_idx[s].append(label)
        for h in prov.get("hooks", []):
            hooks_idx[h].append(label)
        for v in req.get("env_vars", []):
            env_vars_idx[v].append(label)
        feature_requires[label] = req.get("features", [])

    # Short-name → label map for dependency resolution
    short_to_label = {
        label.split("splent_feature_", 1)[-1].split("@")[0]: label
        for label, _ in labeled_contracts
    }

    # 🚨 Route conflicts
    for route, labels in routes_idx.items():
        if len(labels) > 1:
            findings.append(
                {
                    "severity": "error",
                    "field": "routes",
                    "values": [route],
                    "features": sorted(labels),
                    "message": (
                        f"Route '{route}' is registered by {len(labels)} features. "
                        f"Flask will raise an AssertionError at startup."
                    ),
                }
            )

    # 🚨 Blueprint name conflicts
    for bp, labels in blueprints_idx.items():
        if len(labels) > 1:
            findings.append(
                {
                    "severity": "error",
                    "field": "blueprints",
                    "values": [bp],
                    "features": sorted(labels),
                    "message": (
                        f"Blueprint name '{bp}' declared by {len(labels)} features. "
                        f"The second registration will conflict."
                    ),
                }
            )

    # ⚠️ Model name conflicts
    for model, labels in models_idx.items():
        if len(labels) > 1:
            findings.append(
                {
                    "severity": "warning",
                    "field": "models",
                    "values": [model],
                    "features": sorted(labels),
                    "message": (
                        f"Model class '{model}' defined by {len(labels)} features. "
                        f"Verify that __tablename__ values differ."
                    ),
                }
            )

    # ⚠️ Service name conflicts
    for svc, labels in services_idx.items():
        if len(labels) > 1:
            findings.append(
                {
                    "severity": "warning",
                    "field": "services",
                    "values": [svc],
                    "features": sorted(labels),
                    "message": (
                        f"Service class '{svc}' defined by {len(labels)} features. "
                        f"May cause import ambiguity."
                    ),
                }
            )

    # ⚠️ Missing dependencies (requires a feature not declared in product)
    for label, required_shorts in feature_requires.items():
        for short in required_shorts:
            if short not in short_to_label:
                findings.append(
                    {
                        "severity": "warning",
                        "field": "requires",
                        "values": [f"splent_feature_{short}"],
                        "features": [label],
                        "message": (
                            f"{label} requires 'splent_feature_{short}' "
                            f"but it is not declared in pyproject.toml."
                        ),
                    }
                )

    # ℹ️ Shared hook slots (additive, not a bug, but worth reviewing)
    for slot, labels in hooks_idx.items():
        if len(labels) > 1:
            findings.append(
                {
                    "severity": "info",
                    "field": "hooks",
                    "values": [slot],
                    "features": sorted(labels),
                    "message": (
                        f"Hook slot '{slot}' has {len(labels)} registered callbacks. "
                        f"Both will run — verify the combined output in your layouts."
                    ),
                }
            )

    # ℹ️ Shared env vars
    for var, labels in env_vars_idx.items():
        if len(labels) > 1:
            findings.append(
                {
                    "severity": "info",
                    "field": "env_vars",
                    "values": [var],
                    "features": sorted(labels),
                    "message": (
                        f"Env var '{var}' required by {len(labels)} features. "
                        f"One .env entry serves all of them."
                    ),
                }
            )

    return sorted(findings, key=lambda f: SEVERITY_ORDER[f["severity"]])


# ─────────────────────────────────────────────────────────────────────────────
# Programmatic API
# ─────────────────────────────────────────────────────────────────────────────


def run_all_product_check(workspace: str, product_dir: str) -> list[dict]:
    """
    Run the all-product compatibility check programmatically.
    Returns the findings list (may be empty). Does not print anything.
    """
    try:
        labeled_paths = _resolve_all_product_features(product_dir, workspace)
    except SystemExit:
        return []
    if not labeled_paths:
        return []
    labeled_contracts = [(lbl, _read_contract(path)) for lbl, path in labeled_paths]
    return _analyse_all(labeled_contracts)


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────


def _print_findings(findings: list[dict], min_severity: str) -> None:
    min_level = SEVERITY_ORDER[min_severity.lower()]
    findings = [f for f in findings if SEVERITY_ORDER[f["severity"]] <= min_level]
    findings.sort(key=lambda f: SEVERITY_ORDER[f["severity"]])

    if not findings:
        click.secho("  ✅ No conflicts detected.", fg="green")
        click.echo()
        return

    for finding in findings:
        icon = SEVERITY_ICON[finding["severity"]]
        field = click.style(f"[{finding['field']}]", fg="bright_black")
        click.echo(f"  {icon}  {field}")
        click.echo(f"           {finding['message']}")
        if len(finding.get("features", [])) > 1:
            feats = click.style(", ".join(finding["features"]), fg="bright_black")
            click.echo(f"           Features: {feats}")
        click.echo()

    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    infos = [f for f in findings if f["severity"] == "info"]

    parts = []
    if errors:
        parts.append(click.style(f"{len(errors)} error(s)", fg="red", bold=True))
    if warnings:
        parts.append(click.style(f"{len(warnings)} warning(s)", fg="yellow", bold=True))
    if infos:
        parts.append(click.style(f"{len(infos)} info", fg="cyan"))

    click.echo(click.style(f"  {'─' * 70}", fg="bright_black"))
    click.echo("  " + "  •  ".join(parts))
    click.echo()


# ─────────────────────────────────────────────────────────────────────────────
# Command
# ─────────────────────────────────────────────────────────────────────────────


@click.command(
    "feature:diff",
    short_help="Compare feature contracts and report conflicts.",
)
@click.argument("feature_ref_a", required=False)
@click.argument("feature_ref_b", required=False)
@click.option(
    "--all", "check_all", is_flag=True, help="Check all features in the active product."
)
@click.option("--json", "as_json", is_flag=True, help="Output findings as JSON.")
@click.option(
    "--min-severity",
    type=click.Choice(["error", "warning", "info"], case_sensitive=False),
    default="info",
    show_default=True,
    help="Minimum severity level to display.",
)
def feature_diff(feature_ref_a, feature_ref_b, check_all, as_json, min_severity):
    """
    Compare feature contracts and report potential conflicts.

    \b
    Two-feature mode:
      splent feature:diff splent_feature_auth splent_feature_profile

    All-product mode (checks every feature pair at once):
      splent feature:diff --all

    \b
    Severity levels:
      🚨 ERROR   Route or Blueprint name collision — will break Flask at startup.
      ⚠️  WARNING Model/Service name collision, or missing declared dependency.
      ℹ️  INFO    Shared hook slots or env vars — additive, worth reviewing.
    """
    workspace = str(context.workspace())

    # ── All-product mode ──────────────────────────────────────────────────────
    if check_all:
        product = context.require_app()
        product_dir = os.path.join(workspace, product)

        labeled_paths = _resolve_all_product_features(product_dir, workspace)
        if not labeled_paths:
            click.echo("  No features resolved.")
            return

        labeled_contracts = [(lbl, _read_contract(path)) for lbl, path in labeled_paths]

        n = len(labeled_contracts)
        pairs = n * (n - 1) // 2

        if as_json:
            findings = _analyse_all(labeled_contracts)
            click.echo(json.dumps(findings, indent=2))
            return

        click.echo()
        click.echo(
            click.style(
                f"  Compatibility check — {product}  ({n} features, {pairs} pair{'s' if pairs != 1 else ''})",
                bold=True,
            )
        )
        click.echo(click.style(f"  {'─' * 70}", fg="bright_black"))
        click.echo()

        findings = _analyse_all(labeled_contracts)
        _print_findings(findings, min_severity)
        return

    # ── Two-feature mode ──────────────────────────────────────────────────────
    if not feature_ref_a or not feature_ref_b:
        raise click.UsageError(
            "Provide two feature refs for pair comparison, or use --all for the full product."
        )

    cache_a, _, name_a, ver_a = _resolve_feature(feature_ref_a, workspace)
    cache_b, _, name_b, ver_b = _resolve_feature(feature_ref_b, workspace)

    label_a = f"{name_a}@{ver_a}" if ver_a else name_a
    label_b = f"{name_b}@{ver_b}" if ver_b else name_b

    contract_a = _read_contract(cache_a)
    contract_b = _read_contract(cache_b)

    findings = _analyse_pair(contract_a, contract_b, label_a, label_b)

    if as_json:
        min_level = SEVERITY_ORDER[min_severity.lower()]
        click.echo(
            json.dumps(
                [f for f in findings if SEVERITY_ORDER[f["severity"]] <= min_level],
                indent=2,
            )
        )
        return

    click.echo()
    click.echo(click.style(f"  Contract diff — {label_a}  vs  {label_b}", bold=True))
    click.echo(click.style(f"  {'─' * 70}", fg="bright_black"))

    if contract_a["description"]:
        click.echo(
            f"  {label_a}: {click.style(contract_a['description'], fg='bright_black')}"
        )
    if contract_b["description"]:
        click.echo(
            f"  {label_b}: {click.style(contract_b['description'], fg='bright_black')}"
        )
    click.echo()

    _print_findings(findings, min_severity)


cli_command = feature_diff
