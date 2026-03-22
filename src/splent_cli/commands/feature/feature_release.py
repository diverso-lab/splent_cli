import os
import re
import subprocess
import tomllib
import click
from pathlib import Path

from splent_cli.commands.feature.feature_attach import feature_attach
from splent_cli.services import context, release


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# =====================================================================
# PARSER: [namespace/]name[@version]
# =====================================================================
def parse_feature_ref(ref: str, default_ns: str = DEFAULT_NAMESPACE):
    m = re.match(r"^(?:(?P<ns>[^/@]+)/)?(?P<name>[^@]+?)(?:@(?P<ver>.+))?$", ref)
    if not m:
        raise ValueError(f"❌ Invalid feature format: {ref}")

    ns = m.group("ns") or default_ns
    name = m.group("name")
    ver = m.group("ver")
    return ns, name, ver


# =====================================================================
# ENVIRONMENT VALIDATION
# =====================================================================
def validate_environment():
    missing = []

    if not os.getenv("SPLENT_APP"):
        missing.append("SPLENT_APP")

    if not os.getenv("GITHUB_TOKEN"):
        click.echo("⚠️ Warning: GITHUB_TOKEN not set → GitHub release will be skipped.")

    pypi_user = os.getenv("TWINE_USERNAME") or os.getenv("PYPI_USERNAME")
    pypi_pass = os.getenv("TWINE_PASSWORD") or os.getenv("PYPI_PASSWORD")

    if not pypi_user:
        missing.append("TWINE_USERNAME or PYPI_USERNAME")

    if not pypi_pass:
        missing.append("TWINE_PASSWORD or PYPI_PASSWORD")

    if missing:
        click.echo("❌ Missing required environment variables:")
        for m in missing:
            click.echo(f"   - {m}")
        raise SystemExit(1)


# =====================================================================
# LOCATE EDITABLE DIRECTORY (base name without version suffix)
# =====================================================================
def resolve_feature_path(feature_ref: str, version_arg: str, workspace: str):
    ns, name, ver_in_ref = parse_feature_ref(feature_ref)

    if ver_in_ref:
        raise SystemExit(
            f"❌ Cannot release a versioned reference: '{feature_ref}'.\n"
            f"   Use: {ns}/{name}"
        )

    cache_base = os.path.join(
        workspace, ".splent_cache", "features", ns.replace("-", "_")
    )
    base_dir = os.path.join(cache_base, name)

    if not os.path.exists(base_dir):
        raise SystemExit(
            f"❌ Editable feature not found at:\n"
            f"   {base_dir}\n\n"
            f"Run: splent feature:clone {ns}/{name}"
        )

    return base_dir, ns, name, version_arg.lstrip("v")


# =====================================================================
# CONTRACT AUTO-INFERENCE
# =====================================================================

def _extract_routes(routes_path: Path) -> list[str]:
    """Extract route paths from routes.py via regex."""
    if not routes_path.exists():
        return []
    text = routes_path.read_text()
    return sorted(set(re.findall(r"""@\w+\.route\s*\(\s*['"]([^'"]+)['"]""", text)))


def _extract_blueprints(init_path: Path) -> list[str]:
    """Extract blueprint variable names from __init__.py via regex."""
    if not init_path.exists():
        return []
    text = init_path.read_text()
    return sorted(set(re.findall(r"""(\w+)\s*=\s*(?:BaseBlueprint|Blueprint)\s*\(""", text)))


def _extract_models(models_path: Path) -> list[str]:
    """Extract SQLAlchemy model class names from models.py via regex."""
    if not models_path.exists():
        return []
    text = models_path.read_text()
    return sorted(set(re.findall(r"""class\s+(\w+)\s*\([^)]*db\.Model[^)]*\)""", text)))


def _scan_dependencies(src_dir: Path, own_feature_name: str) -> tuple[list[str], list[str]]:
    """
    Scan all .py files under src_dir for:
    - imports of other splent features  → requires.features
    - os.getenv / os.environ references → requires.env_vars
    """
    required_features: set[str] = set()
    env_vars: set[str] = set()

    for py_file in src_dir.rglob("*.py"):
        text = py_file.read_text()

        for short_name in re.findall(r"splent_feature_(\w+)", text):
            if f"splent_feature_{short_name}" != own_feature_name:
                required_features.add(short_name)

        for var in re.findall(
            r"""os\.(?:getenv|environ\.get)\s*\(\s*['"]([A-Z][A-Z0-9_]+)['"]""", text
        ):
            env_vars.add(var)
        for var in re.findall(r"""os\.environ\s*\[\s*['"]([A-Z][A-Z0-9_]+)['"]""", text):
            env_vars.add(var)

    return sorted(required_features), sorted(env_vars)


def infer_contract(feature_path: str, namespace: str, feature_name: str) -> dict:
    """
    Introspect feature source code and return a contract dict with:
      provides: routes, blueprints, models, commands
      requires: features, env_vars
    """
    src_dir = (
        Path(feature_path) / "src" / namespace.replace("-", "_") / feature_name
    )
    routes = _extract_routes(src_dir / "routes.py")
    blueprints = _extract_blueprints(src_dir / "__init__.py")
    models = _extract_models(src_dir / "models.py")
    req_features, env_vars = _scan_dependencies(src_dir, feature_name)

    return {
        "routes": routes,
        "blueprints": blueprints,
        "models": models,
        "commands": [],
        "requires_features": req_features,
        "env_vars": env_vars,
    }


def write_contract(pyproject_path: str, contract: dict, feature_name: str) -> None:
    """
    Rewrite [tool.splent.contract] in pyproject.toml.
    The developer's description is preserved; all other fields are auto-generated.
    """
    path = Path(pyproject_path)
    text = path.read_text()

    # Preserve any existing description the developer may have written
    existing_description = f"{feature_name} feature"
    try:
        data = tomllib.loads(text)
        desc = (
            data.get("tool", {})
            .get("splent", {})
            .get("contract", {})
            .get("description")
        )
        if desc:
            existing_description = desc
    except Exception:
        pass

    # Strip old contract block (from [tool.splent.contract] to EOF)
    match = re.search(r"^\[tool\.splent\.contract\b", text, re.MULTILINE)
    if match:
        text = text[: match.start()].rstrip()

    def _toml_list(items: list[str]) -> str:
        if not items:
            return "[]"
        return "[" + ", ".join(f'"{i}"' for i in items) + "]"

    contract_block = (
        "\n\n"
        "# ── Feature Contract (auto-generated by splent feature:release) ───────────────\n"
        "# Do not edit manually — re-run `splent feature:release` to refresh.\n"
        "[tool.splent.contract]\n"
        f'description = "{existing_description}"\n'
        "\n"
        "[tool.splent.contract.provides]\n"
        f"routes     = {_toml_list(contract['routes'])}\n"
        f"blueprints = {_toml_list(contract['blueprints'])}\n"
        f"models     = {_toml_list(contract['models'])}\n"
        f"commands   = {_toml_list(contract['commands'])}\n"
        "\n"
        "[tool.splent.contract.requires]\n"
        f"features = {_toml_list(contract['requires_features'])}\n"
        f"env_vars = {_toml_list(contract['env_vars'])}\n"
    )

    path.write_text(text + contract_block)


# =====================================================================
# VERSIONED SNAPSHOT
# =====================================================================
def create_versioned_snapshot(namespace, feature_name, version, workspace):
    org_github = namespace.replace("_", "-")

    cache_root = os.path.join(workspace, ".splent_cache", "features", namespace)
    snapshot_path = os.path.join(cache_root, f"{feature_name}@{version}")

    clone_url = f"git@github.com:{org_github}/{feature_name}.git"

    click.echo(f"📥 Creating versioned snapshot: {snapshot_path}")
    click.echo(f"🔗 GitHub repo: {clone_url}")

    subprocess.run(
        ["git", "clone", "--branch", version, "--depth", "1", clone_url, snapshot_path],
        check=True,
    )

    click.echo("✅ Snapshot created.")


# =====================================================================
# COMMAND
# =====================================================================
@click.command(
    "feature:release",
    short_help="Release a feature: bump version, tag, publish to GitHub/PyPI, and snapshot.",
)
@click.argument("feature_ref")
@click.argument("version")
@click.option("--attach", is_flag=True)
def feature_release(feature_ref, version, attach):
    validate_environment()

    workspace = str(context.workspace())

    feature_path, namespace, feature_name, normalized = resolve_feature_path(
        feature_ref, version, workspace
    )

    click.echo(f"🚀 Releasing {namespace}/{feature_name}@{version}")

    click.echo("🔍 Inferring feature contract from source code...")
    contract = infer_contract(feature_path, namespace, feature_name)
    write_contract(os.path.join(feature_path, "pyproject.toml"), contract, feature_name)
    click.echo("✅ Contract written to pyproject.toml.")

    release.update_version(os.path.join(feature_path, "pyproject.toml"), normalized)
    release.commit_local_changes(feature_path, version)

    release.create_and_push_git_tag(feature_path, version)
    remote_url = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        cwd=feature_path,
    ).stdout.strip()
    repo = release.extract_repo(remote_url)
    release.create_github_release(repo, version, os.getenv("GITHUB_TOKEN"))

    release.build_and_upload_pypi(feature_path)

    create_versioned_snapshot(namespace, feature_name, version, workspace)

    if attach:
        click.echo("🔗 Attaching to product...")
        ctx = click.get_current_context()
        ctx.invoke(feature_attach, feature_identifier=feature_ref, version=version)

    click.echo("🎉 Release completed!")
