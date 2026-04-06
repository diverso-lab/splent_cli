"""Tests for the check:pyproject command and its _find_missing_pkgs helper."""
import pytest
from click.testing import CliRunner

from splent_cli.commands.check.check_pyproject import check_pyproject, _find_missing_pkgs


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# _find_missing_pkgs helper
# ---------------------------------------------------------------------------

class TestFindMissingPkgs:
    def test_installed_package_not_reported(self):
        # click is always installed in tests
        result = _find_missing_pkgs(["click"])
        assert "click" not in result

    def test_missing_package_is_reported(self):
        result = _find_missing_pkgs(["definitely_not_installed_xyz_99"])
        assert "definitely_not_installed_xyz_99" in result

    def test_empty_list(self):
        assert _find_missing_pkgs([]) == []

    def test_version_specifier_stripped(self):
        # "click>=8.0" → checks "click" which is installed
        result = _find_missing_pkgs(["click>=8.0"])
        assert "click" not in result

    def test_multiple_deps_mixed(self):
        result = _find_missing_pkgs(["click", "definitely_missing_abc"])
        assert "click" not in result
        assert "definitely_missing_abc" in result

    def test_empty_string_skipped(self):
        # Should not crash on empty strings
        result = _find_missing_pkgs([""])
        assert result == []


# ---------------------------------------------------------------------------
# check:pyproject command
# ---------------------------------------------------------------------------

class TestCheckPyproject:
    def test_exits_when_no_splent_app(self, runner, workspace):
        result = runner.invoke(check_pyproject)
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output

    def test_exits_when_pyproject_missing(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").unlink()
        result = runner.invoke(check_pyproject)
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output

    def test_parses_valid_pyproject(self, runner, product_workspace):
        result = runner.invoke(check_pyproject)
        assert "parsed successfully" in result.output

    def test_reports_project_name(self, runner, product_workspace):
        result = runner.invoke(check_pyproject)
        assert "test_app" in result.output

    def test_reports_project_version(self, runner, product_workspace):
        result = runner.invoke(check_pyproject)
        assert "1.0.0" in result.output

    def test_no_features_declared_shows_zero(self, runner, product_workspace):
        result = runner.invoke(check_pyproject)
        assert "0 features" in result.output

    def test_features_declared_shows_count(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[tool.splent]\nfeatures = ["splent-io/splent_feature_auth"]\n'
        )
        result = runner.invoke(check_pyproject)
        assert "1 features" in result.output

    def test_no_spl_configured_shows_warning(self, runner, product_workspace):
        result = runner.invoke(check_pyproject)
        assert "No SPL configured" in result.output

    def test_uvl_file_found_shows_ok(self, runner, product_workspace):
        uvl_dir = product_workspace / "test_app" / "uvl"
        uvl_dir.mkdir()
        (uvl_dir / "model.uvl").write_text("features\nconstraints\n")
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\nfeatures = []\n'
            '[tool.splent.uvl]\nfile = "model.uvl"\n'
        )
        result = runner.invoke(check_pyproject)
        assert "UVL file found" in result.output

    def test_uvl_file_missing_shows_fail(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\nfeatures = []\n'
            '[tool.splent.uvl]\nfile = "missing.uvl"\n'
        )
        result = runner.invoke(check_pyproject)
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_no_version_shows_warning(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\n'
            '[project.optional-dependencies]\nfeatures = []\n'
        )
        result = runner.invoke(check_pyproject)
        assert "version" in result.output.lower()

    def test_no_name_shows_warning(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\nfeatures = []\n'
        )
        result = runner.invoke(check_pyproject)
        assert "name" in result.output.lower()
