import os
import importlib
from splent_cli.utils.path_utils import PathUtils
import click


def _missing_dependency(exc: Exception) -> str | None:
    """The third-party package a command needs and cannot find.

    A ModuleNotFoundError naming something outside splent means the
    environment is missing a declared dependency, which is a different
    problem from a broken command and has a single fix. Anything else is
    reported as-is.
    """
    if not isinstance(exc, ModuleNotFoundError) or not exc.name:
        return None
    root = exc.name.split(".")[0]
    if root.startswith("splent"):
        return None
    return root


def load_commands(cli_group):
    commands_path = PathUtils.get_commands_path()
    # Missing package -> the commands it made unavailable. Several commands
    # import the same package, so reporting each one separately prints the
    # same line over and over on every invocation.
    missing_deps: dict[str, list[str]] = {}

    for root, _, files in os.walk(commands_path):
        for file in files:
            if file.endswith(".py") and not file.startswith("__"):
                # Build the fully-qualified module name from the relative path
                rel_path = os.path.relpath(os.path.join(root, file), commands_path)
                module_name = (
                    "splent_cli.commands." + rel_path.replace(os.sep, ".")[:-3]
                )

                try:
                    module = importlib.import_module(module_name)
                except Exception as e:
                    dependency = _missing_dependency(e)
                    if dependency:
                        missing_deps.setdefault(dependency, []).append(module_name)
                    else:
                        click.secho(
                            f"⚠  Skipping {module_name}: {e}", fg="yellow", err=True
                        )
                    if os.getenv("SPLENT_DEBUG"):
                        import traceback

                        traceback.print_exc()
                    continue

                # Prefer an explicit declaration when present. A module may
                # name one command, or several when a command answers to more
                # than one name.
                declared = getattr(module, "cli_commands", None)
                if declared is None:
                    declared = getattr(module, "cli_command", None)
                if declared is not None:
                    commands = (
                        declared if isinstance(declared, (list, tuple)) else [declared]
                    )
                    added = False
                    for command in commands:
                        if isinstance(command, click.Command):
                            cli_group.add_command(command)
                            added = True
                    if added:
                        continue

                # Fall back to scanning all module attributes
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, click.Command):
                        cli_group.add_command(attr)

    _report_missing_dependencies(missing_deps)


def _reinstall_hint() -> str:
    """How to reinstall THIS CLI, editable checkout or published package.

    A workspace checkout is upgraded from its own directory; anything else
    came from PyPI and is upgraded from there.
    """
    from splent_cli.utils.path_utils import _BasePathUtils

    try:
        cli_root = os.path.join(_BasePathUtils.get_working_dir(), "splent_cli")
    except Exception:
        cli_root = ""
    if cli_root and os.path.isfile(os.path.join(cli_root, "pyproject.toml")):
        return f"pip install -e {cli_root}"
    return "pip install --upgrade splent_cli"


def _report_missing_dependencies(missing_deps: dict[str, list[str]]) -> None:
    """One actionable line per missing package, not one per command.

    The commands are simply absent from --help when this happens, so saying
    how many were dropped and how to get them back is the whole point.
    """
    for dependency, modules in sorted(missing_deps.items()):
        count = len(modules)
        commands = "command" if count == 1 else "commands"
        click.secho(
            f"⚠  {count} {commands} unavailable: this environment has no "
            f"'{dependency}' package.",
            fg="yellow",
            err=True,
        )
        click.secho(
            "   The CLI is running newer code than the environment it was "
            "installed into. Reinstall it with its dependencies:",
            fg="yellow",
            err=True,
        )
        click.secho(f"     {_reinstall_hint()}", fg="yellow", err=True)
        if os.getenv("SPLENT_DEBUG"):
            for module_name in sorted(modules):
                click.secho(f"     - {module_name}", fg="yellow", err=True)
