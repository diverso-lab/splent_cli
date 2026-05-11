import os
import re
import subprocess
import tomllib
import click
import requests
from splent_cli.services import context, compose, marketplace
from splent_cli.services.api_client import (
    SplentAPIAuthError,
    SplentAPIError,
    get_packages,
    get_package_by_name,
)
from splent_cli.utils.feature_utils import read_features_from_data


def _get_required_features(feature_pyproject: str) -> list[str]:
    """Read [tool.splent.contract.requires].features from a feature's pyproject.toml."""
    if not os.path.isfile(feature_pyproject):
        return []
    with open(feature_pyproject, "rb") as f:
        data = tomllib.load(f)
    return (
        data.get("tool", {})
        .get("splent", {})
        .get("contract", {})
        .get("requires", {})
        .get("features", [])
    )


def _get_product_feature_shorts(
    workspace: str, product: str, env_name: str
) -> set[str]:
    """Return short names of all features currently in the product."""
    py_path = os.path.join(workspace, product, "pyproject.toml")
    if not os.path.isfile(py_path):
        return set()
    with open(py_path, "rb") as f:
        data = tomllib.load(f)
    entries = read_features_from_data(data, env_name)
    shorts = set()
    for entry in entries:
        name = entry.split("/")[-1] if "/" in entry else entry
        name = name.split("@")[0]
        name = name.replace("splent_feature_", "")
        shorts.add(name)
    return shorts


def _feature_short_name(feature_ref: str) -> str:
    name = str(feature_ref).strip()
    if not name:
        return ""
    name = name.split("/")[-1]
    name = name.split("@", 1)[0]
    return name.replace("splent_feature_", "")


def _feature_api_name(feature_name: str) -> str:
    if "/" in feature_name:
        owner, name = feature_name.split("/", 1)
        if name.startswith("splent_feature_"):
            return f"{owner}/{name}"
        return f"{owner}/splent_feature_{name}"

    if feature_name.startswith("splent_feature_"):
        return feature_name
    return f"splent_feature_{feature_name}"


def _feature_api_candidates(feature_name: str) -> list[str]:
    if "/" in feature_name:
        candidates = [feature_name]
        normalized = _feature_api_name(feature_name)
        if normalized not in candidates:
            candidates.append(normalized)
        short = normalized.split("/", 1)[1]
        if short not in candidates:
            candidates.append(short)
        return candidates

    candidates = [_feature_api_name(feature_name)]
    if feature_name not in candidates:
        candidates.append(feature_name)
    return candidates


def _package_matches_candidate(package: dict, candidate: str) -> bool:
    values = {
        str(package.get("name") or ""),
        str(package.get("full_name") or ""),
        str(package.get("repository") or ""),
    }
    return candidate in values


def _get_marketplace_package(feature_identifier: str) -> dict:
    candidates = _feature_api_candidates(feature_identifier)
    last_lookup_error = None
    for candidate in candidates:
        try:
            package = get_package_by_name(candidate)
        except SplentAPIError as exc:
            last_lookup_error = exc
            if "HTTP 404" in str(exc) or "HTTP 500" in str(exc):
                continue
            raise
        if isinstance(package, dict):
            return package

    try:
        packages = get_packages()
    except SplentAPIError:
        if last_lookup_error:
            raise last_lookup_error
        raise

    if isinstance(packages, list):
        for package in packages:
            if isinstance(package, dict) and any(
                _package_matches_candidate(package, candidate)
                for candidate in candidates
            ):
                return package

    raise SplentAPIError(
        f"Feature '{feature_identifier}' is not published in the Marketplace."
    )


def _get_marketplace_required_features(package: dict) -> list[str]:
    contract = package.get("contract") or {}
    requires = contract.get("requires") or {}
    raw_features = requires.get("features") or []

    if isinstance(raw_features, str):
        raw_features = [raw_features]
    if not isinstance(raw_features, list):
        return []

    return [
        short
        for short in (_feature_short_name(feature) for feature in raw_features)
        if short
    ]


def _check_marketplace_dependencies(
    workspace: str,
    product: str,
    env_name: str,
    package: dict,
) -> list[str]:
    required = _get_marketplace_required_features(package)
    if not required:
        return []

    installed = _get_product_feature_shorts(workspace, product, env_name)
    return [feature for feature in required if feature not in installed]


def _abort_missing_marketplace_dependencies(
    short: str,
    product: str,
    namespace_github: str,
    missing: list[str],
) -> None:
    if not missing:
        return

    click.echo()
    click.secho(
        f"  Cannot install {short}: missing required feature(s) in {product}.",
        fg="red",
    )
    for feature in missing:
        click.echo(f"    - {feature}")
    click.echo()
    click.echo("  Install them first:")
    for feature in missing:
        click.echo(f"    splent feature:install {namespace_github}/splent_feature_{feature}")
    raise SystemExit(1)


def _find_feature_pyproject(
    workspace: str, feature_name: str, namespace_fs: str, version: str | None
) -> str | None:
    """Locate the pyproject.toml of a feature (editable or cached)."""
    # Editable at workspace root
    candidate = os.path.join(workspace, feature_name, "pyproject.toml")
    if os.path.isfile(candidate):
        return candidate
    # Pinned in cache
    if version:
        candidate = os.path.join(
            workspace,
            ".splent_cache",
            "features",
            namespace_fs,
            f"{feature_name}@{version}",
            "pyproject.toml",
        )
        if os.path.isfile(candidate):
            return candidate
    return None


def _get_contract_env(feature_pyproject: str) -> str | None:
    """Read [tool.splent.contract].env from a feature's pyproject.toml.

    Returns "dev", "prod", or None (all environments).
    """
    if not os.path.isfile(feature_pyproject):
        return None
    with open(feature_pyproject, "rb") as f:
        data = tomllib.load(f)
    return data.get("tool", {}).get("splent", {}).get("contract", {}).get("env")


def _check_dependencies(workspace, product, env_name, feature_pyproject, short):
    """Check if required features are present in the product. Returns list of missing."""
    required = _get_required_features(feature_pyproject)
    if not required:
        return []
    installed = _get_product_feature_shorts(workspace, product, env_name)
    missing = [r for r in required if r not in installed]
    return missing


def _get_available_versions(namespace: str, repo: str, limit: int = 10) -> list[str]:
    """Fetch semver tags from GitHub, sorted newest first."""
    api_url = f"https://api.github.com/repos/{namespace}/{repo}/tags?per_page=100"
    try:
        r = requests.get(api_url, timeout=5)
        r.raise_for_status()
        tags = r.json()
        versions = []
        for tag in tags:
            name = tag.get("name", "")
            m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", name)
            if m:
                versions.append(
                    (int(m.group(1)), int(m.group(2)), int(m.group(3)), name)
                )
        versions.sort(reverse=True)
        return [v[3] for v in versions[:limit]]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return []


@click.command(
    "feature:install",
    short_help="Install a feature into the active product (interactive).",
)
@click.argument("feature_identifier", required=True)
@click.option(
    "--dev",
    "env_scope",
    flag_value="dev",
    help="Add to features_dev (development only).",
)
@click.option(
    "--prod",
    "env_scope",
    flag_value="prod",
    help="Add to features_prod (production only).",
)
@click.option(
    "--editable",
    "mode",
    flag_value="editable",
    help="Clone as editable at workspace root.",
)
@click.option(
    "--pinned",
    "mode",
    flag_value="pinned",
    help="Pin a released version from cache.",
)
@click.option(
    "--version",
    "version",
    default=None,
    help="Version to pin (e.g. v1.2.3). Only for pinned mode.",
)
def feature_install(feature_identifier, env_scope, mode, version):
    """
    Install a feature into the active product in one step.

    Combines clone + add/attach + env merge + Docker service startup.

    \b
    feature:add/feature:attach handle pyproject.toml, symlinks, and
    hot-reinstall (pip + Flask reload) in the web container. This command
    adds: clone if needed, env merge for new variables, and starting
    the feature's own Docker services (if any).

    \b
    Examples:
      splent feature:install splent-io/splent_feature_nginx
      splent feature:install splent-io/splent_feature_nginx --pinned --version v1.0.0
      splent feature:install splent-io/splent_feature_nginx --editable --dev
    """
    product = context.require_app()
    workspace = str(context.workspace())
    env_name = os.getenv("SPLENT_ENV", "dev")

    # ── Parse identifier ──────────────────────────────────────────────
    namespace, namespace_github, namespace_fs, feature_name = (
        compose.parse_feature_identifier(feature_identifier)
    )

    try:
        marketplace.require_marketplace_login()
        package = _get_marketplace_package(feature_identifier)
    except SplentAPIAuthError as exc:
        click.secho(f"❌ {exc}", fg="red")
        raise SystemExit(1)
    except SplentAPIError as exc:
        click.secho(f"❌ {exc}", fg="red")
        click.echo("   Check SPLENT_API_URL or start the package index.")
        raise SystemExit(1)

    if not isinstance(package, dict):
        click.secho("❌ Invalid package response from API.", fg="red")
        raise SystemExit(1)

    package_name = package.get("name") or _feature_api_name(feature_identifier)
    full_name = package.get("full_name")
    if isinstance(full_name, str) and "/" in full_name:
        feature_identifier, _, package_version = full_name.partition("@")
        if package_version and not version:
            version = package_version
    else:
        repository = package.get("repository")
        if isinstance(repository, str) and "/" in repository:
            feature_identifier = repository
        else:
            owner = package.get("owner") or namespace_github
            feature_identifier = f"{owner}/{package_name}"
    namespace, namespace_github, namespace_fs, feature_name = (
        compose.parse_feature_identifier(feature_identifier)
    )
    short = feature_name.replace("splent_feature_", "")
    missing_marketplace_deps = _check_marketplace_dependencies(
        workspace, product, env_name, package
    )
    _abort_missing_marketplace_dependencies(
        short, product, namespace_github, missing_marketplace_deps
    )

    # ── Ask mode if not specified ─────────────────────────────────────
    if not mode:
        editable_exists = os.path.isdir(os.path.join(workspace, feature_name))
        if editable_exists:
            click.echo(f"  {short} found at workspace root.")
            mode = "editable"
        else:
            choice = click.prompt(
                "  Install as",
                type=click.Choice(["editable", "pinned"]),
                default="pinned",
            )
            mode = choice

    click.echo()
    click.echo(
        click.style("  installing ", dim=True)
        + click.style(short, bold=True)
        + click.style(f" ({mode})", dim=True)
        + (click.style(f" [{env_scope}]", fg="yellow") if env_scope else "")
    )

    # ── Step 1: Ensure feature is available locally ───────────────────
    if mode == "pinned":
        # Resolve version
        if not version:
            click.echo(
                click.style("  version  ", dim=True) + "fetching available versions..."
            )
            versions = _get_available_versions(namespace_github, feature_name)
            if not versions:
                click.secho(
                    f"  No versions found for {namespace_github}/{feature_name}.",
                    fg="red",
                )
                raise SystemExit(1)

            # Show versions and let user pick
            click.echo()
            for i, v in enumerate(versions):
                label = click.style(" (latest)", fg="green") if i == 0 else ""
                click.echo(f"    {i + 1})  {v}{label}")
            click.echo()

            choice = click.prompt(
                "  Select version",
                type=click.IntRange(1, len(versions)),
                default=1,
            )
            version = versions[choice - 1]
            click.echo(click.style("  version  ", dim=True) + f"selected {version}")

        # Clone to cache if not present
        cache_dir = os.path.join(
            workspace,
            ".splent_cache",
            "features",
            namespace_fs,
            f"{feature_name}@{version}",
        )
        if not os.path.isdir(cache_dir):
            click.echo(click.style("  clone    ", dim=True) + f"{short}@{version}")
            result = subprocess.run(
                [
                    "splent",
                    "feature:clone",
                    f"{namespace_github}/{feature_name}@{version}",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                click.secho(f"  Clone failed: {result.stderr.strip()}", fg="red")
                raise SystemExit(1)
        else:
            click.echo(
                click.style("  cache    ", dim=True)
                + f"{short}@{version} already cached"
            )

        # Check contract env and dependencies before attaching
        feat_pyproject = _find_feature_pyproject(
            workspace, feature_name, namespace_fs, version
        )
        if feat_pyproject:
            # Auto-detect env scope from contract
            if not env_scope:
                contract_env = _get_contract_env(feat_pyproject)
                if contract_env:
                    env_scope = contract_env
                    click.echo(
                        click.style("  scope    ", dim=True)
                        + f"contract declares env={contract_env} → features_{contract_env}"
                    )

            missing = _check_dependencies(
                workspace, product, env_name, feat_pyproject, short
            )
            if missing:
                click.echo()
                click.secho(
                    f"  {short} requires features not installed in {product}:",
                    fg="yellow",
                )
                for m in missing:
                    click.echo(f"    - {m}")
                click.echo()
                if not click.confirm("  Install them first?", default=True):
                    click.secho("  Aborted.", fg="red")
                    raise SystemExit(1)
                for m in missing:
                    click.echo()
                    subprocess.run(
                        [
                            "splent",
                            "feature:install",
                            f"{namespace_github}/splent_feature_{m}",
                        ],
                        text=True,
                    )

        # Attach to product
        click.echo(click.style("  attach   ", dim=True) + f"{short}@{version}")
        cmd = ["splent", "feature:attach", feature_identifier, version]
        if env_scope:
            cmd.append(f"--{env_scope}")
        subprocess.run(cmd, capture_output=True, text=True)

    elif mode == "editable":
        feature_dir = os.path.join(workspace, feature_name)
        if not os.path.isdir(feature_dir):
            # Clone to workspace root as editable
            click.echo(click.style("  clone    ", dim=True) + f"{short} (editable)")
            from splent_cli.commands.feature.feature_clone import (
                _build_repo_url,
                _get_latest_tag,
            )

            tag = _get_latest_tag(namespace_github, feature_name)
            repo_url, _ = _build_repo_url(namespace_github, feature_name)
            branch = tag or "main"
            try:
                subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--branch",
                        branch,
                        "--quiet",
                        repo_url,
                        feature_dir,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError:
                click.secho(
                    f"  Could not clone {namespace_github}/{feature_name}.", fg="red"
                )
                raise SystemExit(1)

        # Check contract env and dependencies before adding
        feat_pyproject = _find_feature_pyproject(
            workspace, feature_name, namespace_fs, None
        )
        if feat_pyproject:
            # Auto-detect env scope from contract
            if not env_scope:
                contract_env = _get_contract_env(feat_pyproject)
                if contract_env:
                    env_scope = contract_env
                    click.echo(
                        click.style("  scope    ", dim=True)
                        + f"contract declares env={contract_env} → features_{contract_env}"
                    )

            missing = _check_dependencies(
                workspace, product, env_name, feat_pyproject, short
            )
            if missing:
                click.echo()
                click.secho(
                    f"  {short} requires features not installed in {product}:",
                    fg="yellow",
                )
                for m in missing:
                    click.echo(f"    - {m}")
                click.echo()
                if not click.confirm("  Install them first?", default=True):
                    click.secho("  Aborted.", fg="red")
                    raise SystemExit(1)
                for m in missing:
                    click.echo()
                    subprocess.run(
                        [
                            "splent",
                            "feature:install",
                            f"{namespace_github}/splent_feature_{m}",
                        ],
                        text=True,
                    )

        # Add to product
        click.echo(click.style("  add      ", dim=True) + f"{short}")
        full_name = f"{namespace_github}/{feature_name}"
        cmd = ["splent", "feature:add", full_name]
        if env_scope:
            cmd.append(f"--{env_scope}")
        subprocess.run(cmd, capture_output=True, text=True)

    # ── Step 2: Env generate + merge ──────────────────────────────────
    # feature:add/attach already handled pyproject, symlink, and hot_reinstall.
    # We still need env merge for new variables (e.g. NGINX_UPSTREAM_HOST).
    click.echo(click.style("  env      ", dim=True) + "generate + merge")
    subprocess.run(
        ["splent", "product:env", "--generate", "--all", f"--{env_name}"],
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["splent", "product:env", "--merge", f"--{env_name}"],
        capture_output=True,
        text=True,
    )

    # ── Step 3: Start feature Docker services (if any) ────────────────
    # hot_reinstall already reloaded Flask in the web container.
    # Here we only start the feature's own Docker services (nginx, redis, etc.)
    clean_ref = compose.normalize_feature_ref(
        f"{namespace_github}/{feature_name}" + (f"@{version}" if version else "")
    )
    docker_dir_f = compose.feature_docker_dir(workspace, clean_ref)
    base_path = os.path.dirname(docker_dir_f)
    compose_file = compose.resolve_file(base_path, env_name)

    if compose_file:
        proj = compose.project_name(f"{namespace_fs}/{feature_name}", env_name)
        click.echo(click.style("  docker   ", dim=True) + f"starting {short} services")
        result = subprocess.run(
            ["docker", "compose", "-p", proj, "-f", compose_file, "up", "-d"],
            cwd=docker_dir_f,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            click.secho(f"  Docker failed: {result.stderr.strip()}", fg="yellow")

    click.echo()
    click.secho(f"  {short} installed.", fg="green")
