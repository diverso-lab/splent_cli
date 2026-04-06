"""
Tests for the feature:remove command.
"""
import os
import tomllib
import tomli_w
import pytest
from click.testing import CliRunner

from splent_cli.commands.feature.feature_remove import feature_remove


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(feature_remove, ["my_feature"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output

    def test_missing_pyproject_exits(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        (tmp_path / "test_app").mkdir()
        # No pyproject.toml

        result = runner.invoke(feature_remove, ["my_feature"])
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output


# ---------------------------------------------------------------------------
# Removing from pyproject.toml
# ---------------------------------------------------------------------------

class TestPyprojectUpdate:
    def _write_pyproject(self, path, features):
        data = {
            "project": {
                "name": "test_app",
                "version": "1.0.0",
            },
            "tool": {"splent": {"features": features}},
        }
        with open(path, "wb") as f:
            tomli_w.dump(data, f)

    def test_removes_existing_feature(self, runner, product_workspace):
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(pyproject, ["my_feature"])

        result = runner.invoke(feature_remove, ["my_feature"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        features = data["tool"]["splent"]["features"]
        assert "my_feature" not in features

    def test_feature_not_found_shows_info(self, runner, product_workspace):
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(pyproject, ["other_feature"])

        result = runner.invoke(feature_remove, ["my_feature"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower() or "ℹ️" in result.output

    def test_namespaced_feature_removed(self, runner, product_workspace, monkeypatch):
        """With --namespace, entry is ns_safe/feature_name."""
        monkeypatch.delenv("GITHUB_USER", raising=False)
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(pyproject, ["myorg/my_feature"])

        result = runner.invoke(feature_remove, ["my_feature", "--namespace", "myorg"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()


# ---------------------------------------------------------------------------
# Symlink removal
# ---------------------------------------------------------------------------

class TestSymlinkRemoval:
    def test_removes_existing_symlink(self, runner, product_workspace, monkeypatch):
        # Ensure predictable org: no GITHUB_USER → org = "splent-io" → org_safe = "splent_io"
        monkeypatch.delenv("GITHUB_USER", raising=False)
        link_dir = product_workspace / "test_app" / "features" / "splent_io"
        link_dir.mkdir(parents=True)
        target = product_workspace / "some_feature"
        target.mkdir()
        link = link_dir / "my_feature"
        link.symlink_to(target)

        result = runner.invoke(feature_remove, ["my_feature"])
        assert result.exit_code == 0
        assert not link.is_symlink()

    def test_missing_symlink_does_not_crash(self, runner, product_workspace, monkeypatch):
        monkeypatch.delenv("GITHUB_USER", raising=False)
        result = runner.invoke(feature_remove, ["nonexistent_feature"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Success message
# ---------------------------------------------------------------------------

class TestSuccessMessage:
    def test_always_shows_success_at_end(self, runner, product_workspace):
        result = runner.invoke(feature_remove, ["my_feature"])
        assert result.exit_code == 0
        assert "done" in result.output.lower()
