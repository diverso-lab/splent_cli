"""Tests for the check:features command."""
import os
import pytest
from pathlib import Path
from click.testing import CliRunner

from splent_cli.commands.check.check_features import check_features, _pkg_installed


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _add_feature_to_pyproject(product_workspace, feature_spec):
    p = product_workspace / "test_app" / "pyproject.toml"
    p.write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n'
        f'[project.optional-dependencies]\nfeatures = ["{feature_spec}"]\n'
    )


def _make_feature_cache(workspace, org_safe, name, version):
    """Create a minimal cache entry with proper src/ structure."""
    d = (
        workspace / ".splent_cache" / "features"
        / org_safe / f"{name}@{version}"
        / "src" / org_safe / name
    )
    d.mkdir(parents=True)
    return d


def _make_feature_symlink(product_workspace, org_safe, name, version, target):
    link_dir = product_workspace / "test_app" / "features" / org_safe
    link_dir.mkdir(parents=True, exist_ok=True)
    link = link_dir / f"{name}@{version}"
    rel = os.path.relpath(str(target.parent.parent.parent.parent.parent), str(link_dir))
    link.symlink_to(os.path.relpath(str(target.parent.parent.parent.parent), str(link_dir)))
    return link


# ---------------------------------------------------------------------------
# _pkg_installed helper
# ---------------------------------------------------------------------------

class TestPkgInstalled:
    def test_installed_package_returns_true(self):
        assert _pkg_installed("click") is True

    def test_missing_package_returns_false(self):
        assert _pkg_installed("definitely_not_installed_zxq") is False


# ---------------------------------------------------------------------------
# Command: no SPLENT_APP
# ---------------------------------------------------------------------------

class TestCheckFeaturesNoApp:
    def test_exits_when_no_splent_app(self, runner, workspace):
        result = runner.invoke(check_features)
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output

    def test_exits_when_pyproject_missing(self, runner, product_workspace):
        (product_workspace / "test_app" / "pyproject.toml").unlink()
        result = runner.invoke(check_features)
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output


# ---------------------------------------------------------------------------
# No features declared
# ---------------------------------------------------------------------------

class TestCheckFeaturesNoFeatures:
    def test_warns_and_exits_ok_when_no_features(self, runner, product_workspace):
        result = runner.invoke(check_features)
        assert result.exit_code == 0
        assert "No features" in result.output


# ---------------------------------------------------------------------------
# Feature cache/dir checks
# ---------------------------------------------------------------------------

class TestCheckFeaturesWithFeature:
    def test_fails_when_cache_entry_missing(self, runner, product_workspace):
        _add_feature_to_pyproject(product_workspace, "splent_io/auth@v1.0.0")
        result = runner.invoke(check_features)
        assert result.exit_code == 1
        assert "Not found" in result.output

    def test_fails_when_src_structure_missing(self, runner, product_workspace):
        # Create cache dir but without src/ substructure
        cache_dir = (
            product_workspace / ".splent_cache" / "features"
            / "splent_io" / "auth@v1.0.0"
        )
        cache_dir.mkdir(parents=True)
        _add_feature_to_pyproject(product_workspace, "splent_io/auth@v1.0.0")
        result = runner.invoke(check_features)
        assert result.exit_code == 1
        assert "missing src" in result.output

    def test_ok_when_cache_and_src_exist(self, runner, product_workspace):
        src = _make_feature_cache(product_workspace, "splent_io", "auth", "v1.0.0")
        _add_feature_to_pyproject(product_workspace, "splent_io/auth@v1.0.0")
        result = runner.invoke(check_features)
        assert "Found in cache" in result.output

    def test_fails_when_no_symlink(self, runner, product_workspace):
        _make_feature_cache(product_workspace, "splent_io", "auth", "v1.0.0")
        _add_feature_to_pyproject(product_workspace, "splent_io/auth@v1.0.0")
        result = runner.invoke(check_features)
        assert result.exit_code == 1
        assert "No symlink" in result.output

    def test_feature_label_shown_in_output(self, runner, product_workspace):
        _add_feature_to_pyproject(product_workspace, "splent_io/auth@v1.0.0")
        result = runner.invoke(check_features)
        assert "splent_io/auth" in result.output

    def test_all_ok_message_shown_on_success(self, runner, product_workspace):
        src = _make_feature_cache(product_workspace, "splent_io", "auth", "v1.0.0")
        # Create symlink
        link_dir = product_workspace / "test_app" / "features" / "splent_io"
        link_dir.mkdir(parents=True)
        link = link_dir / "auth@v1.0.0"
        cache_entry = product_workspace / ".splent_cache" / "features" / "splent_io" / "auth@v1.0.0"
        link.symlink_to(os.path.relpath(str(cache_entry), str(link_dir)))
        _add_feature_to_pyproject(product_workspace, "splent_io/auth@v1.0.0")
        result = runner.invoke(check_features)
        assert "All features OK" in result.output

    def test_editable_feature_checks_workspace_root(self, runner, product_workspace):
        # Editable feature (no version) → workspace root
        _add_feature_to_pyproject(product_workspace, "splent_io/auth")
        result = runner.invoke(check_features)
        assert "workspace root" in result.output
