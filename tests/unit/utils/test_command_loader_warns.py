"""
Tests for splent_cli.utils.command_loader.load_commands.

Hardened behavior under test:
  - A command module that FAILS TO IMPORT is SKIPPED with a one-line warning
    on STDERR that names the offending module. This must happen ALWAYS (not
    only when SPLENT_DEBUG is set), and the rest of the CLI must still load.

The loader walks PathUtils.get_commands_path() for *.py files, derives a
module name "splent_cli.commands.<...>", and importlib.import_module()s each.
We avoid touching the real commands tree (and avoid importing real plugins)
by pointing get_commands_path() at a tmp dir and stubbing import_module.
"""

import types

import click
import pytest

from splent_cli.utils import command_loader


def _make_commands_dir(tmp_path, filenames):
    """Create a fake commands dir with the given *.py files (empty contents)."""
    cmds = tmp_path / "commands"
    cmds.mkdir()
    for name in filenames:
        (cmds / name).write_text("")
    return cmds


def _good_module_with_command(cmd_name):
    """A real module object exposing one click.Command."""
    mod = types.ModuleType("fake_good_module")

    @click.command(name=cmd_name)
    def _cmd():
        pass

    mod.some_command = _cmd
    return mod


def _install_loader(monkeypatch, commands_dir, import_side_effect):
    """Point the loader at commands_dir and stub its import_module."""
    monkeypatch.setattr(
        command_loader.PathUtils, "get_commands_path", lambda: str(commands_dir)
    )
    monkeypatch.setattr(command_loader.importlib, "import_module", import_side_effect)


@pytest.fixture
def cli_group():
    @click.group()
    def cli():
        pass

    return cli


class TestBrokenModuleIsSkipped:
    def test_broken_module_warns_on_stderr_and_does_not_raise(
        self, tmp_path, monkeypatch, capsys
    ):
        cmds = _make_commands_dir(tmp_path, ["broken.py"])

        def fake_import(module_name):
            raise ImportError("boom: missing dependency")

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        # Must not propagate the import error.
        command_loader.load_commands(group)

        err = capsys.readouterr().err
        # The offending module is named in the warning.
        assert "splent_cli.commands.broken" in err
        # The warning is a single line.
        assert len([ln for ln in err.splitlines() if ln.strip()]) == 1
        # Clean message: no traceback leaked to stderr by default.
        assert "Traceback" not in err
        assert "ImportError" not in err

    def test_broken_module_does_not_register_a_command(self, tmp_path, monkeypatch):
        cmds = _make_commands_dir(tmp_path, ["broken.py"])

        def fake_import(module_name):
            raise ImportError("boom")

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        command_loader.load_commands(group)
        assert group.commands == {}

    def test_warning_emitted_without_splent_debug(self, tmp_path, monkeypatch, capsys):
        # The warning must appear even when SPLENT_DEBUG is unset.
        monkeypatch.delenv("SPLENT_DEBUG", raising=False)
        cmds = _make_commands_dir(tmp_path, ["broken.py"])

        def fake_import(module_name):
            raise RuntimeError("kaboom")

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        command_loader.load_commands(group)

        err = capsys.readouterr().err
        assert "splent_cli.commands.broken" in err
        # No traceback without debug.
        assert "Traceback" not in err

    def test_splent_debug_adds_traceback_but_still_skips(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("SPLENT_DEBUG", "1")
        cmds = _make_commands_dir(tmp_path, ["broken.py"])

        def fake_import(module_name):
            raise RuntimeError("kaboom")

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        # Still must not raise.
        command_loader.load_commands(group)

        err = capsys.readouterr().err
        # Warning line still present...
        assert "splent_cli.commands.broken" in err
        # ...and under debug the traceback is shown.
        assert "Traceback" in err


class TestRestOfCliStillLoads:
    def test_good_module_loads_when_another_module_is_broken(
        self, tmp_path, monkeypatch, capsys
    ):
        cmds = _make_commands_dir(tmp_path, ["broken.py", "good.py"])

        def fake_import(module_name):
            if module_name.endswith(".broken"):
                raise ImportError("broken on purpose")
            return _good_module_with_command("hello")

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        command_loader.load_commands(group)

        # The healthy command is registered despite the broken sibling.
        assert "hello" in group.commands
        err = capsys.readouterr().err
        assert "splent_cli.commands.broken" in err


class TestHappyPath:
    def test_registers_command_from_module_attribute(self, tmp_path, monkeypatch):
        cmds = _make_commands_dir(tmp_path, ["good.py"])

        def fake_import(module_name):
            return _good_module_with_command("ping")

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        command_loader.load_commands(group)
        assert "ping" in group.commands

    def test_prefers_explicit_cli_command_attribute(self, tmp_path, monkeypatch):
        cmds = _make_commands_dir(tmp_path, ["good.py"])

        @click.command(name="explicit")
        def explicit_cmd():
            pass

        mod = types.ModuleType("fake_explicit")
        mod.cli_command = explicit_cmd

        def fake_import(module_name):
            return mod

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        command_loader.load_commands(group)
        assert "explicit" in group.commands

    def test_ignores_dunder_and_non_py_files(self, tmp_path, monkeypatch):
        cmds = tmp_path / "commands"
        cmds.mkdir()
        (cmds / "__init__.py").write_text("")
        (cmds / "notes.txt").write_text("not python")
        (cmds / "real.py").write_text("")

        seen = []

        def fake_import(module_name):
            seen.append(module_name)
            return _good_module_with_command("real")

        _install_loader(monkeypatch, cmds, fake_import)

        group = click.Group()
        command_loader.load_commands(group)

        # Only the real .py module was imported.
        assert seen == ["splent_cli.commands.real"]
        assert "real" in group.commands
