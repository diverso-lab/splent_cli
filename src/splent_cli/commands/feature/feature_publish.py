import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import click
import tomllib

from splent_cli.commands.feature.feature_contract import _resolve_feature, infer_contract
from splent_cli.services import context
from splent_cli.services.api_client import SplentAPIError, post
from splent_cli.utils.feature_utils import normalize_namespace

DEFAULT_OWNER = "splent-io"


def _read_pyproject(feature_path: Path) -> dict:
    pyproject_path = feature_path / "pyproject.toml"
    if not pyproject_path.exists():
        return {}

    with open(pyproject_path, "rb") as f:
        return tomllib.load(f)


def _git_remote_url(feature_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(feature_path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        url = result.stdout.strip()
        return url or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _parse_full_name(full_name: str) -> tuple[str, str, str | None]:
    # Separa owner, nombre y versión; sin owner usa splent-io.
    if "/" in full_name:
        owner, rest = full_name.split("/", 1)
    else:
        owner = DEFAULT_OWNER
        rest = full_name

    name, _, version = rest.partition("@")
    return owner.replace("_", "-"), name, version or None


def _safe_remote_url(remote_url: str | None) -> str | None:
    if not remote_url:
        return None

    # Evita publicar tokens si el remote HTTPS los incluye.
    return re.sub(r"https://[^/@]+@", "https://", remote_url)


def _repo_from_remote(remote_url: str | None) -> tuple[str | None, str | None]:
    if not remote_url:
        return None, None

    # Extrae owner/repo desde remotes SSH o HTTPS de GitHub.
    patterns = [
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"^https://(?:[^/@]+@)?github\.com/(?P<owner>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, remote_url)
        if match:
            return match.group("owner"), match.group("repo")

    parsed = urlparse(remote_url)
    if parsed.netloc.endswith("github.com"):
        parts = parsed.path.strip("/").split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1].removesuffix(".git")

    return None, None


def _canonical_full_name(owner: str, name: str, version: str | None) -> str:
    ref = f"{owner}/{name}"
    if version:
        return f"{ref}@{version}"
    return ref


def _contract_for_marketplace(contract: dict, pyproject: dict, name: str) -> dict:
    # Convierte el contrato inferido al formato que consume el marketplace/API.
    current_contract = (
        pyproject.get("tool", {}).get("splent", {}).get("contract", {})
        if isinstance(pyproject, dict)
        else {}
    )
    project = pyproject.get("project", {}) if isinstance(pyproject, dict) else {}

    provides = {
        "routes": contract.get("routes", []),
        "blueprints": contract.get("blueprints", []),
        "models": contract.get("models", []),
        "commands": contract.get("commands", []),
        "hooks": contract.get("hooks", []),
        "services": contract.get("services", []),
        "signals": contract.get("signals", []),
        "translations": contract.get("translations", []),
        "docker": contract.get("docker", []),
    }
    requires = {
        "features": contract.get("requires_features", []),
        "env_vars": contract.get("env_vars", []),
        "signals": contract.get("requires_signals", []),
    }

    return {
        "description": (
            current_contract.get("description")
            or project.get("description")
            or f"{name} feature"
        ),
        "provides": provides,
        "requires": requires,
        "extensible": {
            "services": contract.get("extensible_services", []),
            "templates": contract.get("extensible_templates", []),
            "models": contract.get("extensible_models", []),
            "hooks": contract.get("extensible_hooks", []),
            "routes": contract.get("extensible_routes", False),
        },
        "docker": contract.get("docker_contract", {}),
    }


def _build_payload(
    feature_path: Path,
    namespace: str,
    name: str,
    full_name: str,
    owner: str | None = None,
) -> dict:
    ref_owner, _, ref_version = _parse_full_name(full_name)
    github_owner = (owner or ref_owner).replace("_", "-")

    # Construye un payload compatible con la API actual y con metadatos extendidos.
    inferred_contract = infer_contract(str(feature_path), namespace, name)
    pyproject = _read_pyproject(feature_path)
    marketplace_contract = _contract_for_marketplace(inferred_contract, pyproject, name)

    project = pyproject.get("project", {}) if isinstance(pyproject, dict) else {}
    remote_url = _git_remote_url(feature_path)
    repo_owner, repo_name = _repo_from_remote(remote_url)
    repo_owner = repo_owner or github_owner
    repo_name = repo_name or name
    repo_url = _safe_remote_url(remote_url) or f"https://github.com/{repo_owner}/{repo_name}.git"
    canonical_name = _canonical_full_name(github_owner, name, ref_version)

    return {
        "full_name": canonical_name,
        "name": name,
        "description": marketplace_contract["description"],
        "provides": marketplace_contract["provides"],
        "requires": marketplace_contract["requires"],
        "namespace": namespace,
        "owner": github_owner,
        "repo_url": repo_url,
        "repository": f"{repo_owner}/{repo_name}",
        "github": {
            "owner": github_owner,
            "repo": repo_name,
            "repository": f"{repo_owner}/{repo_name}",
            "url": repo_url,
        },
        "contract": marketplace_contract,
        "metadata": {
            "project_name": project.get("name"),
            "version": project.get("version"),
            "feature_version": ref_version,
            "description": project.get("description"),
            "workspace_path": str(feature_path),
            "pyproject_present": bool(pyproject),
            "source": "splent-cli",
        },
    }


def _login_to_marketplace(token: str | None) -> None:
    # Login simple por consola: guarda el token solo para esta ejecución.
    if token:
        os.environ["SPLENT_API_TOKEN"] = token
        return

    if os.getenv("SPLENT_API_TOKEN"):
        return

    click.echo("Marketplace login")
    token = click.prompt(
        "  API token (leave empty for local/dev API)",
        default="",
        hide_input=True,
        show_default=False,
    ).strip()
    if token:
        os.environ["SPLENT_API_TOKEN"] = token


@click.command("feature:publish", short_help="Publish feature metadata to the marketplace.")
@click.argument("full_name", required=True)
@click.option(
    "--token",
    default=None,
    help="Marketplace API token. If omitted, SPLENT_API_TOKEN is used.",
)
@click.option(
    "--owner",
    default=None,
    help="GitHub user or organization that owns the feature repository.",
)
def feature_publish(full_name, token, owner):
    workspace = str(context.workspace())

    try:
        _login_to_marketplace(token)
        feature_path, namespace, name = _resolve_feature(full_name, workspace)
        namespace = normalize_namespace(namespace)
        payload = _build_payload(feature_path, namespace, name, full_name, owner)

        click.echo()
        click.secho(f"Publishing {payload['full_name']}...", bold=True)
        click.echo(f"  repository: {payload['repository']}")
        click.echo(f"  owner:      {payload['owner']}")

        response = post("/api/packages", json=payload)

        click.secho("Feature published successfully.", fg="green")
        if isinstance(response, dict) and response:
            click.echo(click.style("Response:", bold=True))
            click.echo(str(response))

    except SplentAPIError as exc:
        click.secho(f"❌ {exc}", fg="red")
        raise SystemExit(1)
    except SystemExit:
        raise
    except Exception as exc:
        click.secho(f"❌ Unexpected error: {exc}", fg="red")
        raise SystemExit(1)


cli_command = feature_publish
