"""Integration smoke tests for the top-level SPLENT CLI.

These guard against import-time breakage across the whole command tree:
- the ``cli`` Group must import and its built-in commands must load via
  ``command_loader.load_commands`` without raising;
- ``splent --help`` must exit 0 and render the grouped help;
- ``splent version --json`` must produce parseable JSON.

No docker / git / network / database are required. The ``version`` command
reads versions from pyproject.toml files inside a temporary WORKING_DIR, so
we point it at a minimal workspace built under tmp_path.
"""
import json

import click

from splent_cli.cli import cli, SPLENTCLI
from splent_cli.utils.command_loader import load_commands


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cli_workspace(tmp_path, cli_version="9.9.9"):
    """Create a minimal workspace with splent_cli/pyproject.toml under tmp_path.

    The version command resolves the CLI version from
    ``WORKING_DIR/splent_cli/pyproject.toml`` (the editable-install source of
    truth), so a single pyproject.toml is enough to drive a deterministic run.
    """
    pkg_dir = tmp_path / "splent_cli"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "pyproject.toml").write_text(
        f'[project]\nname = "splent_cli"\nversion = "{cli_version}"\n'
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Group / command-tree integrity
# ---------------------------------------------------------------------------

def test_cli_is_splentcli_group():
    """The exported ``cli`` object is the hardened SPLENTCLI Group."""
    assert isinstance(cli, click.Group)
    assert isinstance(cli, SPLENTCLI)


def test_builtin_commands_loaded():
    """load_commands discovered and registered built-in commands on the group.

    A non-trivial command set proves every command module imported cleanly at
    collection time (load_commands skips broken modules silently, so an empty
    or tiny set would signal mass import breakage).
    """
    builtin = cli.commands
    assert builtin, "no built-in commands registered on the CLI group"
    # 'version' is a core built-in and must always be present.
    assert "version" in builtin
    assert isinstance(builtin["version"], click.Command)


def test_load_commands_is_idempotent_on_fresh_group():
    """load_commands can populate a fresh Group without raising.

    Re-running the loader against a brand-new SPLENTCLI must not blow up on
    any command module import; it should register the same core commands.
    """
    fresh = SPLENTCLI(name="cli")
    load_commands(fresh)
    assert "version" in fresh.commands


# ---------------------------------------------------------------------------
# --help smoke
# ---------------------------------------------------------------------------

def test_help_exits_zero_and_lists_groups(runner):
    """``splent --help`` renders grouped help cleanly and exits 0."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    out = result.output
    # Usage banner + at least one category header from format_commands.
    assert "Usage:" in out
    assert "commands available." in out
    # No crash leaked into the output.
    assert "Traceback" not in out
    assert result.stderr == "" or "Traceback" not in result.stderr


def test_help_lists_core_command_groups(runner):
    """The grouped help advertises the major command categories."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    out = result.output
    # A few stable category labels emitted by SPLENTCLI.format_commands.
    assert "Feature Management" in out
    assert "Utilities" in out


def test_no_args_shows_usage(runner):
    """Invoking the group with no subcommand shows usage, not a traceback."""
    result = runner.invoke(cli, [])
    # Click groups without a subcommand exit non-zero but must stay clean.
    assert "Usage:" in result.output
    assert "Traceback" not in result.output
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# version --json smoke
# ---------------------------------------------------------------------------

def test_version_json_returns_parseable_json(runner, tmp_path, monkeypatch):
    """``splent version --json`` emits valid JSON to stdout (no product set)."""
    _make_cli_workspace(tmp_path, cli_version="9.9.9")
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.delenv("SPLENT_APP", raising=False)
    monkeypatch.delenv("SPLENT_ENV", raising=False)

    result = runner.invoke(cli, ["version", "--json"])
    assert result.exit_code == 0
    assert "Traceback" not in result.stderr

    payload = json.loads(result.output)
    assert payload["cli"] == "9.9.9"
    assert payload["product"] is None
    assert payload["features"] == []
    assert isinstance(payload["fingerprint"], str) and payload["fingerprint"]


def test_version_json_stdout_is_pure_json(runner, tmp_path, monkeypatch):
    """JSON mode must not interleave human-readable banner text on stdout.

    The whole point of --json is machine-readability: stdout should parse as a
    single JSON document with the documented keys and nothing else.
    """
    _make_cli_workspace(tmp_path, cli_version="2.3.4")
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.delenv("SPLENT_APP", raising=False)

    result = runner.invoke(cli, ["version", "--json"])
    assert result.exit_code == 0

    payload = json.loads(result.output)  # raises if anything else is on stdout
    assert set(
        ["cli", "framework", "python", "compatible", "product", "features", "fingerprint"]
    ).issubset(payload.keys())


def test_version_json_reports_active_product(runner, tmp_path, monkeypatch):
    """With SPLENT_APP set, the JSON snapshot reports the product name/version."""
    _make_cli_workspace(tmp_path, cli_version="9.9.9")
    app_dir = tmp_path / "test_app"
    app_dir.mkdir()
    (app_dir / "pyproject.toml").write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n'
    )
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")

    result = runner.invoke(cli, ["version", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["product"] == {"name": "test_app", "version": "1.0.0"}
