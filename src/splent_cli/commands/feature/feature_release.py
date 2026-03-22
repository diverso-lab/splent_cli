import os
import re
import subprocess
import click
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
# VALIDACIÓN ENV
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
# LOCALIZAR CARPETA EDITABLE (base sin versión)
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
# SNAPSHOT VERSIONADO EN CACHÉ
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
