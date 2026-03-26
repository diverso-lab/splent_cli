"""
Tests for the feature:add command.

Pattern: real filesystem via tmp_path — this command reads/writes pyproject.toml
and creates symlinks. No subprocess to mock.
"""
import pytest
from pathlib import Path
from click.testing import CliRunner

from splent_cli.commands.feature.feature_add import feature_add


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture
def product_workspace_with_feature(product_workspace):
    """
    Extends product_workspace with an editable feature at workspace root.
    Adds: {workspace}/splent_feature_auth/
    """
    # Editable features live at workspace root (not in .splent_cache)
    feature_dir = product_workspace / "splent_feature_auth"
    feature_dir.mkdir(parents=True)
    return product_workspace


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------

class TestArgumentValidation:
    def test_requires_namespace_slash_name(self, runner, product_workspace):
        result = runner.invoke(feature_add, ["splent_feature_auth"])  # no slash
        assert result.exit_code == 1
        assert "Invalid format" in result.output

    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# Workspace root checks
# ---------------------------------------------------------------------------

class TestWorkspaceRootCheck:
    def test_exits_when_not_at_workspace_root(self, runner, product_workspace):
        result = runner.invoke(feature_add, ["splent_io/nonexistent_feature"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# Successful add
# ---------------------------------------------------------------------------

class TestSuccessfulAdd:
    def test_adds_feature_to_pyproject(self, runner, product_workspace_with_feature, tmp_path):
        result = runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        assert result.exit_code == 0

        pyproject_path = tmp_path / "test_app" / "pyproject.toml"
        content = pyproject_path.read_text()
        assert "splent_io/splent_feature_auth" in content

    def test_creates_symlink(self, runner, product_workspace_with_feature, tmp_path):
        runner.invoke(feature_add, ["splent_io/splent_feature_auth"])

        link = tmp_path / "test_app" / "features" / "splent_io" / "splent_feature_auth"
        assert link.is_symlink()

    def test_symlink_points_to_workspace_root(self, runner, product_workspace_with_feature, tmp_path):
        runner.invoke(feature_add, ["splent_io/splent_feature_auth"])

        link = tmp_path / "test_app" / "features" / "splent_io" / "splent_feature_auth"
        expected_target = tmp_path / "splent_feature_auth"
        assert link.resolve() == expected_target.resolve()

    def test_success_message(self, runner, product_workspace_with_feature):
        result = runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        assert result.exit_code == 0
        assert "added successfully" in result.output.lower() or "✅" in result.output


# ---------------------------------------------------------------------------
# Missing pyproject.toml
# ---------------------------------------------------------------------------

class TestMissingPyproject:
    def test_exits_when_pyproject_missing(self, runner, workspace, monkeypatch, tmp_path):
        """Product directory exists but has no pyproject.toml → exit 1."""
        monkeypatch.setenv("SPLENT_APP", "test_app")
        # Feature exists at workspace root
        (tmp_path / "splent_feature_auth").mkdir(parents=True)
        # Product directory exists but NO pyproject.toml
        (tmp_path / "test_app").mkdir()

        result = runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_adding_twice_does_not_duplicate(self, runner, product_workspace_with_feature, tmp_path):
        runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        runner.invoke(feature_add, ["splent_io/splent_feature_auth"])

        pyproject_path = tmp_path / "test_app" / "pyproject.toml"
        content = pyproject_path.read_text()
        count = content.count("splent_io/splent_feature_auth")
        assert count == 1

    def test_second_add_shows_already_present(self, runner, product_workspace_with_feature):
        runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        result = runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        assert result.exit_code == 0
        assert "already present" in result.output.lower()

    def test_existing_symlink_replaced_not_errored(self, runner, product_workspace_with_feature, tmp_path):
        runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        result = runner.invoke(feature_add, ["splent_io/splent_feature_auth"])
        link = tmp_path / "test_app" / "features" / "splent_io" / "splent_feature_auth"
        assert link.is_symlink()


# ---------------------------------------------------------------------------
# Custom namespace
# ---------------------------------------------------------------------------

class TestCustomNamespace:
    def test_custom_namespace_creates_correct_dir(self, runner, product_workspace, tmp_path):
        # Create feature at workspace root
        (tmp_path / "custom_feature").mkdir(parents=True)

        result = runner.invoke(feature_add, ["drorganvidez/custom_feature"])
        assert result.exit_code == 0

        link = tmp_path / "test_app" / "features" / "drorganvidez" / "custom_feature"
        assert link.is_symlink()
