"""
Seven product commands import yaml. When the environment lacks it, the
loader used to print the same skip line for each of them on every single
invocation, and never said what to do about it.
"""

import click

from splent_cli.utils import command_loader


def test_a_missing_third_party_package_is_recognised():
    assert (
        command_loader._missing_dependency(ModuleNotFoundError(name="yaml")) == "yaml"
    )


def test_a_submodule_reports_its_root_package():
    exc = ModuleNotFoundError(name="yaml.parser")
    assert command_loader._missing_dependency(exc) == "yaml"


def test_a_missing_splent_module_is_not_a_dependency_problem():
    exc = ModuleNotFoundError(name="splent_framework.db")
    assert command_loader._missing_dependency(exc) is None


def test_other_import_errors_are_not_treated_as_missing_dependencies():
    assert command_loader._missing_dependency(ValueError("boom")) is None


def test_one_message_per_package_not_per_command(monkeypatch):
    lines = []
    monkeypatch.setattr(
        click, "secho", lambda msg, **kw: lines.append(msg), raising=True
    )
    monkeypatch.setattr(command_loader, "click", click, raising=True)

    command_loader._report_missing_dependencies(
        {"yaml": ["a", "b", "c", "d", "e", "f", "g"]}
    )

    joined = "\n".join(lines)
    assert joined.count("no 'yaml' package") == 1
    assert "7 commands unavailable" in joined
    assert "pip install" in joined


def test_a_single_missing_command_reads_naturally(monkeypatch):
    lines = []
    monkeypatch.setattr(
        click, "secho", lambda msg, **kw: lines.append(msg), raising=True
    )
    monkeypatch.setattr(command_loader, "click", click, raising=True)

    command_loader._report_missing_dependencies({"yaml": ["only_one"]})

    assert "1 command unavailable" in "\n".join(lines)
