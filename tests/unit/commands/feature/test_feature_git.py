"""
Tests for the feature:git command.
"""
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.feature.feature_git import feature_git, _find_feature_root


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _find_feature_root() — pure filesystem helper
# ---------------------------------------------------------------------------

class TestFindFeatureRoot:
    def test_finds_in_product_symlinks(self, tmp_path):
        pkg = "splent_feature_auth"
        # Create a real dir and a symlink in the product features dir
        real_dir = tmp_path / "real_auth"
        real_dir.mkdir()
        features_dir = tmp_path / "test_app" / "features" / "splent_io"
        features_dir.mkdir(parents=True)
        link = features_dir / "splent_feature_auth"
        link.symlink_to(real_dir)

        result = _find_feature_root(pkg, tmp_path, "test_app")
        assert result is not None
        assert result.name == "real_auth"  # resolved symlink

    def test_finds_in_cache(self, tmp_path):
        pkg = "splent_feature_auth"
        cache_dir = tmp_path / ".splent_cache" / "features" / "splent_io" / "splent_feature_auth@v1.0.0"
        cache_dir.mkdir(parents=True)

        result = _find_feature_root(pkg, tmp_path, "test_app")
        assert result == cache_dir

    def test_finds_bare_workspace_dir(self, tmp_path):
        pkg = "splent_feature_auth"
        bare = tmp_path / pkg
        bare.mkdir()

        result = _find_feature_root(pkg, tmp_path, "test_app")
        assert result == bare

    def test_returns_none_when_not_found(self, tmp_path):
        result = _find_feature_root("splent_feature_missing", tmp_path, "test_app")
        assert result is None


# ---------------------------------------------------------------------------
# CLI: validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(feature_git, ["auth", "status"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output

    def test_no_git_args_exits(self, runner, product_workspace):
        result = runner.invoke(feature_git, ["auth"])
        assert result.exit_code == 1
        assert "No git command" in result.output

    def test_feature_not_found_exits(self, runner, product_workspace):
        result = runner.invoke(feature_git, ["nonexistent_feature", "status"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# CLI: successful git execution
# ---------------------------------------------------------------------------

class TestSuccessfulExecution:
    def test_runs_git_in_feature_dir(self, runner, product_workspace):
        # Create feature in cache
        cache_dir = product_workspace / ".splent_cache" / "features" / "splent_io" / "splent_feature_auth"
        cache_dir.mkdir(parents=True)

        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = runner.invoke(feature_git, ["auth", "status"])

        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "git"
        assert "status" in call_args

    def test_exits_with_git_returncode(self, runner, product_workspace):
        cache_dir = product_workspace / ".splent_cache" / "features" / "splent_io" / "splent_feature_auth"
        cache_dir.mkdir(parents=True)

        with patch("subprocess.run", return_value=MagicMock(returncode=1)):
            result = runner.invoke(feature_git, ["auth", "status"])

        assert result.exit_code == 1

    def test_passes_extra_git_args(self, runner, product_workspace):
        cache_dir = product_workspace / ".splent_cache" / "features" / "splent_io" / "splent_feature_auth"
        cache_dir.mkdir(parents=True)

        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            runner.invoke(feature_git, ["auth", "log", "--oneline", "-5"])

        call_args = mock_run.call_args[0][0]
        assert "--oneline" in call_args
        assert "-5" in call_args
