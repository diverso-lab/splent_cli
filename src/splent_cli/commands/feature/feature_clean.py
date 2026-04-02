"""
feature:clean — Detect and remove scaffold stubs from a feature.

Analyzes source files to detect which archetype the feature really is,
then removes files that are empty stubs or not needed by that archetype.
"""

import os
import re

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import normalize_namespace


DEFAULT_NAMESPACE = os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io")

ARCHETYPES = ("full", "light", "config", "service")

ARCHETYPE_LABELS = {
    "full": "Complete domain feature (models, routes, services, templates, migrations)",
    "light": "Lightweight UI feature (routes, hooks, templates)",
    "config": "Infrastructure feature (config.py only)",
    "service": "Backend service (services, config, commands, signals)",
}

# Files that are ALWAYS kept regardless of archetype
PROTECTED = {"__init__.py"}

# Directories to always ignore
IGNORED = {"__pycache__", ".git", ".egg-info"}


# ── Feature resolution ───────────────────────────────────────────────


def _resolve_feature(feature_ref, workspace):
    if "/" in feature_ref:
        ns_raw, rest = feature_ref.split("/", 1)
        ns = normalize_namespace(ns_raw)
    else:
        ns = DEFAULT_NAMESPACE
        rest = feature_ref

    name = rest.split("@")[0]
    path = os.path.join(workspace, name)
    if os.path.isdir(path):
        return path, ns, name

    click.secho(f"  Feature not found: {path}", fg="red")
    raise SystemExit(1)


def _get_src_dir(feature_path, ns, feature_name):
    return os.path.join(feature_path, "src", normalize_namespace(ns), feature_name)


# ── Stub detection ───────────────────────────────────────────────────


def _read(path):
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        return f.read()


def _is_stub_file(filepath, filename):
    """Check if a Python source file is a scaffold stub with no real content."""
    text = _read(filepath)
    if text is None:
        return True

    checks = {
        "models.py": r"db\.Column",
        "routes.py": r"@\w+\.route\s*\(",
        "services.py": r"def\s+(?!__)\w+\s*\(.*\):\s*\n\s+(?!pass\b|\.\.\.|\s*#)",
        "repositories.py": r"def\s+(?!__)\w+\s*\(.*\):\s*\n\s+(?!pass\b|\.\.\.|\s*#)",
        "hooks.py": r"register_template_hook\s*\(",
        "signals.py": r"(?:define_signal|connect_signal)\s*\(",
        "seeders.py": r"self\.seed\s*\(\s*\[(?!\s*\])",
        "forms.py": r"Field\s*\(",
        "commands.py": r"@click\.command\s*\(",
        "config.py": r"os\.(?:getenv|environ)",
    }

    pattern = checks.get(filename)
    if pattern is None:
        return False

    return not re.search(pattern, text, re.MULTILINE)


def _is_stub_dir(dirpath, dirname):
    """Check if a directory is empty or contains only scaffold files."""
    if not os.path.isdir(dirpath):
        return True

    if dirname == "versions":
        # migrations/versions/ — only .gitkeep = stub
        files = [f for f in os.listdir(dirpath) if f.endswith(".py")]
        return len(files) == 0

    if dirname in ("templates", "assets", "translations"):
        # Count real files (not .gitkeep)
        real = 0
        for root, _, files in os.walk(dirpath):
            for f in files:
                if f != ".gitkeep":
                    real += 1
        return real == 0

    return False


# ── Archetype detection ──────────────────────────────────────────────


def _detect_archetype(src_dir):
    """Detect the real archetype from source file contents."""
    models = _read(os.path.join(src_dir, "models.py"))
    routes = _read(os.path.join(src_dir, "routes.py"))
    services = _read(os.path.join(src_dir, "services.py"))

    has_models = models and re.search(r"db\.Column", models)
    has_routes = routes and re.search(r"@\w+\.route\s*\(", routes)
    has_services = services and re.search(
        r"def\s+(?!__)\w+\s*\(.*\):\s*\n\s+(?!pass\b|\.\.\.|\s*#)",
        services,
        re.MULTILINE,
    )

    if has_models:
        return "full"
    if has_routes:
        return "light"
    if has_services:
        return "service"
    return "config"


# ── Expected files per archetype ─────────────────────────────────────


def _expected_files(archetype):
    """Return set of filenames expected for the archetype (src-level only)."""
    always = {"__init__.py", "tests"}

    if archetype == "full":
        return always | {
            "models.py",
            "routes.py",
            "services.py",
            "repositories.py",
            "forms.py",
            "seeders.py",
            "hooks.py",
            "signals.py",
            "commands.py",
            "config.py",
            "templates",
            "assets",
            "migrations",
            "translations",
            "tests",
        }
    if archetype == "light":
        return always | {"routes.py", "hooks.py", "templates", "tests"}
    if archetype == "config":
        return always | {"config.py", "tests"}
    if archetype == "service":
        return always | {
            "services.py",
            "config.py",
            "signals.py",
            "commands.py",
            "tests",
        }
    return always


# ── Find removable items ─────────────────────────────────────────────


def _find_removable(src_dir, archetype):
    """Find files and directories that should be removed.

    Returns list of (path, reason) tuples.
    """
    expected = _expected_files(archetype)
    removable = []

    if not os.path.isdir(src_dir):
        return removable

    for entry in sorted(os.listdir(src_dir)):
        full = os.path.join(src_dir, entry)

        # Never remove protected files or ignored dirs
        if entry in PROTECTED:
            continue
        if entry in IGNORED or entry.endswith(".egg-info"):
            continue

        # Skip tests directory (always kept, but we check inside it)
        if entry == "tests":
            continue

        if entry not in expected:
            # Not needed by this archetype at all
            removable.append((full, "not needed"))
        elif os.path.isfile(full) and _is_stub_file(full, entry):
            # Needed but is a stub
            removable.append((full, "empty stub"))
        elif os.path.isdir(full):
            # Check subdirectories
            if entry == "migrations":
                versions = os.path.join(full, "versions")
                if _is_stub_dir(versions, "versions"):
                    removable.append((full, "no migrations"))
            elif entry in ("templates", "assets", "translations"):
                if _is_stub_dir(full, entry):
                    removable.append((full, "empty"))

    # Check test subdirectories that are stubs
    tests_dir = os.path.join(src_dir, "tests")
    if os.path.isdir(tests_dir):
        # For non-full archetypes, remove test dirs they don't need
        test_dirs_full = {"unit", "integration", "functional", "e2e", "load"}
        test_dirs_light = {"unit", "functional"}
        test_dirs_minimal = {"unit"}

        if archetype == "full":
            needed_tests = test_dirs_full
        elif archetype == "light":
            needed_tests = test_dirs_light
        else:
            needed_tests = test_dirs_minimal

        for td in sorted(os.listdir(tests_dir)):
            td_path = os.path.join(tests_dir, td)
            if (
                os.path.isdir(td_path)
                and td in test_dirs_full
                and td not in needed_tests
            ):
                removable.append((td_path, "not needed"))

    return removable


# ── Display ──────────────────────────────────────────────────────────


def _print_plan(feature_name, archetype, removable, src_dir):
    click.echo()
    click.echo(click.style(f"  feature:clean — {feature_name}", bold=True))
    click.echo(click.style(f"  {'─' * 56}", fg="bright_black"))
    click.echo()
    click.echo(
        click.style("  Detected archetype: ", dim=True)
        + click.style(archetype, fg="cyan", bold=True)
    )
    click.echo(click.style(f"  {ARCHETYPE_LABELS[archetype]}", dim=True))
    click.echo()

    if not removable:
        click.secho("  ✅ Feature is already clean.", fg="green")
        click.echo()
        return

    click.echo(click.style(f"  Will remove ({len(removable)} items):", bold=True))
    click.echo()

    for path, reason in removable:
        rel = os.path.relpath(path, src_dir)
        is_dir = os.path.isdir(path)
        icon = "📁" if is_dir else "  "
        click.echo(
            f"    {icon} {rel}" + click.style(f"  ({reason})", fg="bright_black")
        )
    click.echo()


# ── Command ──────────────────────────────────────────────────────────


@click.command(
    "feature:clean",
    short_help="Remove scaffold stubs and unnecessary files from a feature.",
)
@click.argument("feature_ref")
@click.option("--apply", is_flag=True, help="Actually remove files (default: dry-run).")
@context.requires_product
def feature_clean(feature_ref, apply):
    """Detect the real archetype and remove scaffold stubs.

    \b
    By default this is a dry-run: shows what would be removed.
    Use --apply to actually delete the files.

    \b
    Examples:
      splent feature:clean splent_feature_redis
      splent feature:clean splent_feature_redis --apply
    """
    workspace = str(context.workspace())
    feature_path, ns, feature_name = _resolve_feature(feature_ref, workspace)
    src_dir = _get_src_dir(feature_path, ns, feature_name)

    if not os.path.isdir(src_dir):
        click.secho(f"  Source directory not found: {src_dir}", fg="red")
        raise SystemExit(1)

    archetype = _detect_archetype(src_dir)
    removable = _find_removable(src_dir, archetype)

    _print_plan(feature_name, archetype, removable, src_dir)

    if not removable:
        return

    if not apply:
        click.echo(
            click.style("  Dry-run — ", fg="yellow")
            + "run with "
            + click.style("--apply", bold=True)
            + " to remove these files."
        )
        click.echo()
        return

    if not click.confirm("  Proceed?", default=True):
        return

    import shutil

    removed = 0
    for path, _ in removable:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.isfile(path):
            os.remove(path)
        removed += 1

    click.echo()
    click.secho(f"  ✅ Removed {removed} item(s).", fg="green")

    # Auto-update the contract
    try:
        from splent_cli.commands.feature.feature_contract import update_contract

        update_contract(feature_path, ns, feature_name)
        click.secho("  ✅ Contract updated.", fg="green")
    except Exception:
        click.echo(
            click.style("  ⚠  Could not update contract — run ", fg="yellow")
            + "splent feature:contract --write"
            + click.style(" manually.", fg="yellow")
        )
    click.echo()


cli_command = feature_clean
