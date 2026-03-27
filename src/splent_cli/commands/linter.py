import os
import sys
import subprocess

import click

from splent_cli.utils.path_utils import PathUtils

_TARGETS = ("cli", "framework", "app", "features")


def _resolve_directories(targets: tuple[str, ...]) -> list[str]:
    active = set(targets) if targets else set(_TARGETS)
    dirs = []
    if "cli" in active:
        dirs.append(PathUtils.get_splent_cli_dir())
    if "framework" in active:
        dirs.append(PathUtils.get_core_dir())
    if "app" in active:
        dirs.append(PathUtils.get_app_dir())
    if "features" in active:
        # Only lint editable features (workspace root).
        # Pinned features in .splent_cache are read-only — never lint them.
        workspace = PathUtils.get_working_dir()
        for entry in os.scandir(workspace):
            if (
                entry.is_dir(follow_symlinks=False)
                and entry.name.startswith("splent_feature_")
                and os.path.exists(os.path.join(entry.path, "pyproject.toml"))
            ):
                dirs.append(entry.path)
    return [d for d in dirs if os.path.isdir(d)]


@click.command(
    "linter", short_help="Lint and optionally auto-fix the project with Ruff."
)
@click.option("--fix", is_flag=True, help="Auto-fix lint issues and reformat code.")
@click.option(
    "--target",
    "targets",
    type=click.Choice(_TARGETS),
    multiple=True,
    help="Target(s) to lint. Repeatable. Defaults to all.",
)
def linter(fix, targets):
    """Run Ruff to lint (and optionally auto-fix + reformat) the project.

    Without --fix: reports lint issues and format violations.\n
    With --fix:    auto-fixes lint issues and reformats all code.\n
    Without --target: runs on all targets (cli, framework, app, features).
    """
    directories = _resolve_directories(targets)

    if not directories:
        raise click.ClickException("No valid target directories found.")

    active_targets = sorted(set(targets) if targets else set(_TARGETS))
    click.echo(click.style("\n📦 SPLENT Linter (Ruff)\n", fg="cyan", bold=True))
    click.echo(f"Targets : {', '.join(active_targets)}")
    click.echo()

    # ── Lint ────────────────────────────────────────────────────────────────
    lint_cmd = ["ruff", "check"]
    if fix:
        lint_cmd.append("--fix")

    lint_ok = True
    for directory in directories:
        label = f"{'Fixing' if fix else 'Checking'} {directory}"
        click.echo(click.style(f"🔍 {label}...\n", fg="yellow"))

        result = subprocess.run(lint_cmd + [directory], capture_output=True, text=True)

        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            lint_ok = False
            if output:
                click.echo(click.style(output, fg="red"))
            click.echo(
                click.style(f"❌ Lint issues in {directory}\n", fg="red", bold=True)
            )
        else:
            if output:
                click.echo(output)
            click.echo(click.style(f"✅ {directory} clean.\n", fg="green"))

    # ── Format ──────────────────────────────────────────────────────────────
    format_ok = True
    if fix:
        click.echo(click.style("\n🎨 Reformatting code...\n", fg="blue"))
        for directory in directories:
            result = subprocess.run(
                ["ruff", "format", directory], capture_output=True, text=True
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                format_ok = False
                if output:
                    click.echo(click.style(output, fg="red"))
                click.echo(
                    click.style(
                        f"❌ Format failed for {directory}\n", fg="red", bold=True
                    )
                )
            else:
                if output:
                    click.echo(output)
    else:
        click.echo(click.style("\n🎨 Checking format...\n", fg="blue"))
        for directory in directories:
            result = subprocess.run(
                ["ruff", "format", "--check", directory], capture_output=True, text=True
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                format_ok = False
                if output:
                    click.echo(click.style(output, fg="yellow"))
                click.echo(
                    click.style(
                        f"⚠️  {directory} needs formatting (run with --fix to apply)\n",
                        fg="yellow",
                    )
                )
            else:
                click.echo(click.style(f"✅ {directory} format OK.\n", fg="green"))

    # ── Summary ─────────────────────────────────────────────────────────────
    click.echo()
    if lint_ok and format_ok:
        click.echo(click.style("✔️  All checks passed.\n", fg="cyan", bold=True))
    else:
        click.echo(click.style("✖  Some checks failed.\n", fg="red", bold=True))
        sys.exit(2)
