"""Tests for context.workspace() validation."""
import pytest
from pathlib import Path
from click.testing import CliRunner
import click
from splent_cli.services.context import workspace


class TestWorkspaceValidation:
    def test_returns_path_when_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        result = workspace()
        assert result == tmp_path

    def test_exits_when_path_does_not_exist(self, monkeypatch):
        monkeypatch.setenv(
            "WORKING_DIR", "/nonexistent/path/that/does/not/exist"
        )

        @click.command()
        def cmd():
            workspace()

        result = CliRunner().invoke(cmd)
        assert result.exit_code == 1
        assert (
            "not found" in result.output.lower()
            or "❌" in result.output
        )

    def test_error_message_mentions_source_env(self, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", "/nonexistent/path")

        @click.command()
        def cmd():
            workspace()

        result = CliRunner().invoke(cmd)
        assert (
            "source .env" in result.output
            or "WORKING_DIR" in result.output
        )
