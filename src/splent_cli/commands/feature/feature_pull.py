import subprocess
import click
from pathlib import Path
from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace


def _editable_features(cache_root: Path) -> list:
    """Return list of (namespace, name, path) for all editable (non-versioned) cache entries."""
    result = []
    if not cache_root.exists():
        return result
    for ns_dir in sorted(cache_root.iterdir()):
        if not ns_dir.is_dir():
            continue
        for feat_dir in sorted(ns_dir.iterdir()):
            if feat_dir.is_dir() and "@" not in feat_dir.name:
                result.append((ns_dir.name, feat_dir.name, feat_dir))
    return result


def _git_pull(path: Path) -> tuple:
    """Run git pull in path. Returns (success, message)."""
    git_dir = path / ".git"
    if not git_dir.exists():
        return False, "not a git repo"
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "pull", "--ff-only"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = r.stdout.strip() or r.stderr.strip()
        return r.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except FileNotFoundError:
        return False, "git not found"


@click.command(
    "feature:pull", short_help="Git pull on one or all editable features in cache."
)
@click.argument("feature_ref", required=False)
@context.requires_product
def feature_pull(feature_ref):
    """
    Run git pull on editable (non-versioned) features in the local cache.

    \b
    With no arguments, pulls all editable features.
    With FEATURE_REF (e.g. splent_io/splent_feature_auth), pulls only that one.
    """
    workspace = context.workspace()
    cache_root = workspace / ".splent_cache" / "features"

    all_features = _editable_features(cache_root)

    if not all_features:
        click.secho("ℹ️  No editable features found in cache.", fg="yellow")
        return

    if feature_ref:
        ns_filter = None
        name_filter = feature_ref
        if "/" in feature_ref:
            ns_filter, name_filter = feature_ref.split("/", 1)
            ns_filter = normalize_namespace(ns_filter)

        targets = [
            f
            for f in all_features
            if f[1] == name_filter and (ns_filter is None or f[0] == ns_filter)
        ]
        if not targets:
            click.secho(
                f"⚠️  No editable cache entry found for '{feature_ref}'.", fg="yellow"
            )
            return
    else:
        targets = all_features

    click.secho(f"Pulling {len(targets)} editable feature(s):\n", fg="cyan")

    ok = 0
    for ns, name, path in targets:
        success, msg = _git_pull(path)
        label = click.style(f"{ns}/{name}", bold=True)
        if success:
            status = click.style("✔", fg="green")
            ok += 1
        else:
            status = click.style("✖", fg="red")
        click.echo(f"  {status}  {label}  {msg}")

    click.echo()
    click.secho(
        f"{ok}/{len(targets)} pulled successfully.",
        fg="green" if ok == len(targets) else "yellow",
    )


cli_command = feature_pull
