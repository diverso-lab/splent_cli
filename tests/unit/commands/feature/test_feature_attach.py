"""Tests for the feature:attach command."""
import pytest
from pathlib import Path
from click.testing import CliRunner

from splent_cli.commands.feature.feature_attach import feature_attach


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Missing pyproject.toml
# ---------------------------------------------------------------------------

class TestMissingPyproject:
    def test_exits_when_no_pyproject(self, runner, product_workspace):
        # Remove pyproject.toml from the product
        (product_workspace / "test_app" / "pyproject.toml").unlink()
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output


# ---------------------------------------------------------------------------
# Cache not found
# ---------------------------------------------------------------------------

class TestCacheNotFound:
    def test_exits_when_cache_missing(self, runner, product_workspace):
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert result.exit_code == 1
        assert "not found in cache" in result.output

    def test_suggests_clone_command(self, runner, product_workspace):
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "feature:clone" in result.output


# ---------------------------------------------------------------------------
# Successful attach
# ---------------------------------------------------------------------------

class TestSuccessfulAttach:
    def _setup_cache(self, workspace, namespace="splent_io", name="auth", version="v1.0.0"):
        cache_dir = (
            workspace / ".splent_cache" / "features" / namespace / f"{name}@{version}"
        )
        cache_dir.mkdir(parents=True)
        return cache_dir

    def test_reports_cache_found(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "Cache found" in result.output

    def test_updates_pyproject(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        content = (product_workspace / "test_app" / "pyproject.toml").read_text()
        assert "auth" in content

    def test_creates_symlink(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        link = (
            product_workspace / "test_app" / "features" / "splent_io" / "auth@v1.0.0"
        )
        assert link.is_symlink()

    def test_success_message(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "successfully attached" in result.output

    def test_reports_link_created(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "Linked" in result.output

    def test_idempotent_already_present(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert result.exit_code == 0
        assert "already present" in result.output

    def test_replaces_bare_entry_with_versioned(self, runner, product_workspace):
        # Write pyproject with bare entry (as uvl:sync would produce)
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\nfeatures = ["splent_io/auth"]\n'
        )
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        content = pyproject.read_text()
        assert "splent_io/auth@v1.0.0" in content
        # bare entry should be removed
        assert '"splent_io/auth"' not in content


# ---------------------------------------------------------------------------
# Symlink replacement
# ---------------------------------------------------------------------------

class TestSymlinkReplacement:
    def _setup_cache(self, workspace, namespace="splent_io", name="auth", version="v1.0.0"):
        cache_dir = (
            workspace / ".splent_cache" / "features" / namespace / f"{name}@{version}"
        )
        cache_dir.mkdir(parents=True)
        return cache_dir

    def test_replaces_existing_symlink(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        # Invoke again — should replace symlink without error
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert result.exit_code == 0
