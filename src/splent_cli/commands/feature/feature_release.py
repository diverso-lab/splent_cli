"""
feature:release — Release a feature: bump version, tag, publish to GitHub/PyPI, and snapshot.
"""

import os
import re
import subprocess
import tomllib

import click
from pathlib import Path

from splent_cli.commands.feature.feature_attach import feature_attach
from splent_cli.services import context, release
from splent_cli.utils.feature_utils import normalize_namespace


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")


# ── Ref parsing ───────────────────────────────────────────────────────


def parse_feature_ref(ref: str, default_ns: str = DEFAULT_NAMESPACE):
    m = re.match(r"^(?:(?P<ns>[^/@]+)/)?(?P<name>[^@]+?)(?:@(?P<ver>.+))?$", ref)
    if not m:
        raise ValueError(f"Invalid feature format: {ref}")
    ns = m.group("ns") or default_ns
    name = m.group("name")
    ver = m.group("ver")
    return ns, name, ver


def resolve_feature_path(feature_ref: str, workspace: str):
    """Locate the editable feature directory at workspace root."""
    ns, name, ver_in_ref = parse_feature_ref(feature_ref)

    if ver_in_ref:
        raise SystemExit(
            f"  error: cannot release a versioned reference: '{feature_ref}'\n"
            f"  Use: {ns}/{name}"
        )

    root_dir = os.path.join(workspace, name)
    if os.path.exists(root_dir):
        return root_dir, ns, name

    cache_base = os.path.join(
        workspace, ".splent_cache", "features", normalize_namespace(ns)
    )
    base_dir = os.path.join(cache_base, name)
    if os.path.exists(base_dir):
        return base_dir, ns, name

    raise SystemExit(
        f"  error: editable feature not found at:\n"
        f"    {root_dir}\n\n"
        f"  Create it with: splent feature:create {ns}/{name}"
    )


# ── Contract inference ────────────────────────────────────────────────


def _extract_routes(routes_path: Path) -> list[str]:
    if not routes_path.exists():
        return []
    text = routes_path.read_text()
    return sorted(set(re.findall(r"""@\w+\.route\s*\(\s*['"]([^'"]+)['"]""", text)))


def _extract_blueprints(init_path: Path) -> list[str]:
    if not init_path.exists():
        return []
    text = init_path.read_text()
    return sorted(
        set(re.findall(r"""(\w+)\s*=\s*(?:BaseBlueprint|Blueprint)\s*\(""", text))
    )


def _extract_models(models_path: Path) -> list[str]:
    if not models_path.exists():
        return []
    text = models_path.read_text()
    return sorted(set(re.findall(r"""class\s+(\w+)\s*\([^)]*db\.Model[^)]*\)""", text)))


def _extract_hooks(hooks_path: Path) -> list[str]:
    if not hooks_path.exists():
        return []
    text = hooks_path.read_text()
    return sorted(
        set(re.findall(r"""register_template_hook\s*\(\s*['"]([^'"]+)['"]""", text))
    )


def _extract_services(services_path: Path) -> list[str]:
    if not services_path.exists():
        return []
    text = services_path.read_text()
    return sorted(
        set(
            re.findall(
                r"""class\s+(\w+)\s*\([^)]*(?:BaseService|Service)[^)]*\)""", text
            )
        )
    )


def _extract_templates(src_dir: Path) -> list[str]:
    templates_dir = src_dir / "templates"
    if not templates_dir.exists():
        return []
    return sorted(
        str(p.relative_to(templates_dir))
        for p in templates_dir.rglob("*.html")
        if not p.name.startswith("_")
    )


def _extract_docker(feature_root: Path) -> list[str]:
    found = []
    for pattern in ("docker-compose*.yml", "docker-compose*.yaml"):
        found.extend(p.name for p in feature_root.glob(pattern))
    return sorted(found)


def _extract_translations(translations_dir: Path) -> list[str]:
    if not translations_dir.is_dir():
        return []
    locales = []
    for entry in sorted(translations_dir.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            lc = entry / "LC_MESSAGES"
            if lc.is_dir():
                locales.append(entry.name)
    return locales


def _extract_signals(signals_path: Path) -> tuple[list[str], list[str]]:
    if not signals_path.exists():
        return [], []
    text = signals_path.read_text()
    provided = sorted(
        set(re.findall(r"""define_signal\s*\(\s*['"]([^'"]+)['"]""", text))
    )
    required = sorted(
        set(re.findall(r"""connect_signal\s*\(\s*['"]([^'"]+)['"]""", text))
    )
    return provided, required


def _extract_commands(commands_path: Path) -> list[str]:
    if not commands_path.exists():
        return []
    text = commands_path.read_text()
    return sorted(set(re.findall(r"""@click\.command\s*\(\s*['"]([^'"]+)['"]""", text)))


def _scan_dependencies(
    src_dir: Path, own_feature_name: str
) -> tuple[list[str], list[str]]:
    required_features: set[str] = set()
    env_vars: set[str] = set()

    for py_file in src_dir.rglob("*.py"):
        text = py_file.read_text()
        for line in text.splitlines():
            stripped = line.lstrip()
            if not (stripped.startswith("import ") or stripped.startswith("from ")):
                continue
            for short_name in re.findall(r"splent_feature_(\w+)", line):
                if f"splent_feature_{short_name}" != own_feature_name:
                    required_features.add(short_name)

        for var in re.findall(
            r"""os\.(?:getenv|environ\.get)\s*\(\s*['"]([A-Z][A-Z0-9_]+)['"]""", text
        ):
            env_vars.add(var)
        for var in re.findall(
            r"""os\.environ\s*\[\s*['"]([A-Z][A-Z0-9_]+)['"]""", text
        ):
            env_vars.add(var)

    return sorted(required_features), sorted(env_vars)


def infer_contract(feature_path: str, namespace: str, feature_name: str) -> dict:
    feature_root = Path(feature_path)
    src_dir = feature_root / "src" / normalize_namespace(namespace) / feature_name

    routes = _extract_routes(src_dir / "routes.py")
    blueprints = _extract_blueprints(src_dir / "__init__.py")
    models = _extract_models(src_dir / "models.py")
    hooks = _extract_hooks(src_dir / "hooks.py")
    services = _extract_services(src_dir / "services.py")
    templates = _extract_templates(src_dir)
    commands = _extract_commands(src_dir / "commands.py")
    docker = _extract_docker(feature_root)
    req_features, env_vars = _scan_dependencies(src_dir, feature_name)
    signals_provided, signals_required = _extract_signals(src_dir / "signals.py")
    translations = _extract_translations(src_dir / "translations")

    return {
        "routes": routes,
        "blueprints": blueprints,
        "models": models,
        "commands": commands,
        "hooks": hooks,
        "services": services,
        "signals": signals_provided,
        "translations": translations,
        "docker": docker,
        "requires_features": req_features,
        "env_vars": env_vars,
        "requires_signals": signals_required,
        "extensible_services": services,
        "extensible_templates": templates,
        "extensible_models": models,
        "extensible_hooks": hooks,
        "extensible_routes": bool(blueprints),
    }


def write_contract(pyproject_path: str, contract: dict, feature_name: str) -> None:
    path = Path(pyproject_path)
    text = path.read_text()

    existing_description = f"{feature_name} feature"
    existing_extensible = None
    try:
        data = tomllib.loads(text)
        splent_contract = data.get("tool", {}).get("splent", {}).get("contract", {})
        desc = splent_contract.get("description")
        if desc:
            existing_description = desc
        ext = splent_contract.get("extensible")
        if ext:
            existing_extensible = ext
    except Exception:
        pass

    match = re.search(r"^# ── Feature Contract\b.*$", text, re.MULTILINE)
    if not match:
        match = re.search(r"^\[tool\.splent\.contract\b", text, re.MULTILINE)
    if match:
        text = text[: match.start()].rstrip()

    def _toml_list(items: list[str]) -> str:
        if not items:
            return "[]"
        return "[" + ", ".join(f'"{i}"' for i in items) + "]"

    if existing_extensible:
        ext_services = existing_extensible.get("services", [])
        ext_templates = existing_extensible.get("templates", [])
        ext_models = existing_extensible.get("models", [])
        ext_hooks = existing_extensible.get("hooks", [])
        ext_routes = existing_extensible.get("routes", False)
    else:
        ext_services = contract.get("extensible_services", [])
        ext_templates = contract.get("extensible_templates", [])
        ext_models = contract.get("extensible_models", [])
        ext_hooks = contract.get("extensible_hooks", [])
        ext_routes = contract.get("extensible_routes", False)

    contract_block = (
        "\n\n"
        "# ── Feature Contract (auto-generated) ────────────────────────────────────────\n"
        "# Do not edit manually — re-run `splent feature:contract --write` to refresh.\n"
        "[tool.splent.contract]\n"
        f'description = "{existing_description}"\n'
        "\n"
        "[tool.splent.contract.provides]\n"
        f"routes     = {_toml_list(contract['routes'])}\n"
        f"blueprints = {_toml_list(contract['blueprints'])}\n"
        f"models     = {_toml_list(contract['models'])}\n"
        f"commands   = {_toml_list(contract['commands'])}\n"
        f"hooks      = {_toml_list(contract['hooks'])}\n"
        f"services   = {_toml_list(contract['services'])}\n"
        f"signals    = {_toml_list(contract.get('signals', []))}\n"
        f"translations = {_toml_list(contract.get('translations', []))}\n"
        f"docker     = {_toml_list(contract['docker'])}\n"
        "\n"
        "[tool.splent.contract.requires]\n"
        f"features = {_toml_list(contract['requires_features'])}\n"
        f"env_vars = {_toml_list(contract['env_vars'])}\n"
        f"signals  = {_toml_list(contract.get('requires_signals', []))}\n"
        "\n"
        "[tool.splent.contract.extensible]\n"
        f"services  = {_toml_list(ext_services)}\n"
        f"templates = {_toml_list(ext_templates)}\n"
        f"models    = {_toml_list(ext_models)}\n"
        f"hooks     = {_toml_list(ext_hooks)}\n"
        f"routes    = {'true' if ext_routes else 'false'}\n"
    )

    path.write_text(text + contract_block)


# ── Compile assets ────────────────────────────────────────────────────


def _compile_before_release(feature_name: str):
    click.echo("  compile  building frontend assets...")
    try:
        result = subprocess.run(
            ["splent", "feature:compile", feature_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            click.echo("  compile  done")
        else:
            output = (result.stderr or result.stdout or "")[:200]
            click.secho(f"  compile  skipped: {output}", fg="yellow")
    except subprocess.TimeoutExpired:
        click.secho("  compile  timed out (120s)", fg="yellow")


# ── Versioned snapshot ────────────────────────────────────────────────


def create_versioned_snapshot(namespace, feature_name, version, workspace):
    namespace_fs = normalize_namespace(namespace)
    org_github = namespace.replace("_", "-")

    cache_root = os.path.join(workspace, ".splent_cache", "features", namespace_fs)
    snapshot_path = os.path.join(cache_root, f"{feature_name}@{version}")

    from splent_cli.utils.git_url import build_git_url

    clone_url, display_url = build_git_url(org_github, feature_name)

    click.echo(f"  snapshot cloning {feature_name}@{version}...")

    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--branch",
                version,
                "--depth",
                "1",
                clone_url,
                snapshot_path,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        click.secho(
            f"  error: failed to clone snapshot for {feature_name}@{version}", fg="red"
        )
        raise SystemExit(1)

    from splent_cli.utils.cache_utils import make_feature_readonly

    make_feature_readonly(snapshot_path)

    click.echo(f"  snapshot {snapshot_path} (read-only)")


# ── Command ───────────────────────────────────────────────────────────


@click.command(
    "feature:release",
    short_help="Release a feature: bump version, tag, publish to GitHub/PyPI, and snapshot.",
)
@click.argument("feature_ref")
@click.argument("version", required=False, default=None)
@click.option("--attach", is_flag=True)
@context.requires_product
def feature_release(feature_ref, version, attach):
    workspace = str(context.workspace())

    feature_path, namespace, feature_name = resolve_feature_path(feature_ref, workspace)
    ns_github = namespace.replace("_", "-")

    if not version:
        version = release.semver_wizard(ns_github, feature_name)

    normalized = version.lstrip("v")
    tag = f"v{normalized}"

    def _pre_commit(path, ver):
        click.echo("  contract updating from source code...")
        from splent_cli.commands.feature.feature_contract import update_contract

        update_contract(path, namespace, feature_name)
        click.echo("  contract written to pyproject.toml")

    def _post_pypi(path, ver):
        _compile_before_release(feature_name)
        create_versioned_snapshot(namespace, feature_name, tag, workspace)

    release.run_release_pipeline(
        f"{namespace}/{feature_name}",
        feature_path,
        version,
        pre_commit_hook=_pre_commit,
        post_pypi_hook=_post_pypi,
    )

    if attach:
        click.echo("  attach   linking to product...")
        ctx = click.get_current_context()
        ctx.invoke(feature_attach, feature_identifier=feature_ref, version=tag)


cli_command = feature_release
