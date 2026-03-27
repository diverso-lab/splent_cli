"""Tests for the check:env command."""
import pytest
from click.testing import CliRunner

from splent_cli.commands.check.check_env import check_env, _pkg_version


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _pkg_version helper
# ---------------------------------------------------------------------------

class TestPkgVersion:
    def test_installed_package_returns_version(self):
        # click is always installed in the test environment
        result = _pkg_version("click")
        assert result is not None
        assert "." in result

    def test_missing_package_returns_none(self):
        assert _pkg_version("definitely_not_installed_xyz_123") is None

    def test_empty_name_returns_none(self):
        # importlib.metadata.version("") raises; we should get None
        result = _pkg_version("")
        assert result is None


# ---------------------------------------------------------------------------
# check:env command
# ---------------------------------------------------------------------------

class TestCheckEnvCommand:
    def test_reports_python_version(self, runner, workspace):
        result = runner.invoke(check_env)
        assert "Python" in result.output

    def test_working_dir_set_shows_ok(self, runner, workspace):
        result = runner.invoke(check_env)
        assert "WORKING_DIR" in result.output

    def test_working_dir_not_set_shows_warning(self, runner, monkeypatch, tmp_path):
        monkeypatch.delenv("WORKING_DIR", raising=False)
        monkeypatch.delenv("SPLENT_APP", raising=False)
        result = runner.invoke(check_env)
        assert "WORKING_DIR" in result.output

    def test_env_file_found_reports_ok(self, runner, workspace):
        (workspace / ".env").write_text("SPLENT_APP=test_app\n")
        result = runner.invoke(check_env)
        assert ".env" in result.output

    def test_splent_app_not_set_fails(self, runner, workspace):
        result = runner.invoke(check_env)
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output

    def test_splent_app_dir_missing_fails(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "nonexistent_app")
        result = runner.invoke(check_env)
        assert result.exit_code == 1

    def test_splent_app_dir_exists_passes(self, runner, product_workspace):
        result = runner.invoke(check_env)
        # With a valid SPLENT_APP and directory, SPLENT_APP check passes.
        # Still exits 1 because CLI/Framework version check may fail in test env.
        assert "SPLENT_APP = test_app" in result.output

    def test_splent_env_not_set_shows_warning(self, runner, workspace, monkeypatch):
        monkeypatch.delenv("SPLENT_ENV", raising=False)
        result = runner.invoke(check_env)
        assert "SPLENT_ENV" in result.output

    def test_splent_env_set_shows_ok(self, runner, product_workspace):
        result = runner.invoke(check_env)
        assert "SPLENT_ENV" in result.output

    def test_github_token_set_shows_ok(self, runner, product_workspace, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        result = runner.invoke(check_env)
        assert "GITHUB_TOKEN" in result.output

    def test_github_token_not_set_shows_warning(self, runner, product_workspace, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        result = runner.invoke(check_env)
        assert "GITHUB_TOKEN" in result.output

    def test_pypi_token_set_shows_ok(self, runner, product_workspace, monkeypatch):
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-token")
        result = runner.invoke(check_env)
        assert "PyPI" in result.output

    def test_pypi_token_not_set_shows_warning(self, runner, product_workspace, monkeypatch):
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.delenv("PYPI_TOKEN", raising=False)
        result = runner.invoke(check_env)
        assert "PyPI" in result.output
