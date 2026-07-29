"""
Tests for the feature:remove command.
"""

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

    def test_missing_symlink_does_not_crash(
        self, runner, product_workspace, monkeypatch
    ):
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


# ---------------------------------------------------------------------------
# Env-scoped lists: features_dev / features_prod
# ---------------------------------------------------------------------------


class TestEnvScopedLists:
    def _write_pyproject(self, path, features=None, dev=None, prod=None):
        splent = {}
        if features is not None:
            splent["features"] = features
        if dev is not None:
            splent["features_dev"] = dev
        if prod is not None:
            splent["features_prod"] = prod
        data = {
            "project": {"name": "test_app", "version": "1.0.0"},
            "tool": {"splent": splent},
        }
        with open(path, "wb") as f:
            tomli_w.dump(data, f)

    def _lists(self, path):
        with open(path, "rb") as f:
            return tomllib.load(f)["tool"]["splent"]

    def test_removes_dev_only_entry_without_flags(self, runner, product_workspace):
        """A feature declared only in features_dev must be removable."""
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(
            pyproject, features=[], dev=["splent-io/splent_feature_admin"]
        )

        result = runner.invoke(
            feature_remove, ["splent_feature_admin", "-n", "splent-io"]
        )
        assert result.exit_code == 0
        assert "removed from features_dev" in result.output
        assert self._lists(pyproject)["features_dev"] == []

    def test_removes_prod_only_entry_without_flags(self, runner, product_workspace):
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(
            pyproject, features=[], prod=["splent-io/splent_feature_admin"]
        )

        result = runner.invoke(feature_remove, ["splent_feature_admin"])
        assert result.exit_code == 0
        assert "removed from features_prod" in result.output
        assert self._lists(pyproject)["features_prod"] == []

    def test_dev_flag_restricts_removal(self, runner, product_workspace):
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(
            pyproject,
            features=["splent-io/splent_feature_admin"],
            dev=["splent-io/splent_feature_admin"],
        )

        result = runner.invoke(feature_remove, ["splent_feature_admin", "--dev"])
        assert result.exit_code == 0

        lists = self._lists(pyproject)
        assert lists["features_dev"] == []
        assert lists["features"] == ["splent-io/splent_feature_admin"]

    def test_still_declared_elsewhere_keeps_its_symlink(
        self, runner, product_workspace
    ):
        """--dev must not tear down what the surviving declaration needs."""
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(
            pyproject,
            features=["splent-io/splent_feature_admin"],
            dev=["splent-io/splent_feature_admin"],
        )

        link_dir = product_workspace / "test_app" / "features" / "splent_io"
        link_dir.mkdir(parents=True)
        target = product_workspace / "splent_feature_admin"
        target.mkdir()
        link = link_dir / "splent_feature_admin"
        link.symlink_to(target)

        result = runner.invoke(feature_remove, ["splent_feature_admin", "--dev"])
        assert result.exit_code == 0
        assert link.is_symlink()

    def test_dev_flag_reports_where_the_entry_actually_is(
        self, runner, product_workspace
    ):
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(
            pyproject, features=["splent-io/splent_feature_admin"], dev=[]
        )

        result = runner.invoke(feature_remove, ["splent_feature_admin", "--dev"])
        assert result.exit_code == 0
        assert "not in features_dev" in result.output
        assert "features" in result.output
        # Nothing was removed from the list it really lives in.
        assert self._lists(pyproject)["features"] == ["splent-io/splent_feature_admin"]

    def test_pinned_entry_is_removed(self, runner, product_workspace):
        """The entry may be pinned; the version must not stop the removal."""
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(
            pyproject, features=[], dev=["splent_io/splent_feature_admin@v1.2.0"]
        )

        result = runner.invoke(feature_remove, ["splent_feature_admin"])
        assert result.exit_code == 0
        assert self._lists(pyproject)["features_dev"] == []

    def test_removes_versioned_symlink_too(self, runner, product_workspace):
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        self._write_pyproject(
            pyproject, features=[], dev=["splent-io/splent_feature_admin@v1.2.0"]
        )

        link_dir = product_workspace / "test_app" / "features" / "splent_io"
        link_dir.mkdir(parents=True)
        target = product_workspace / "splent_feature_admin"
        target.mkdir()
        link = link_dir / "splent_feature_admin@v1.2.0"
        link.symlink_to(target)

        result = runner.invoke(feature_remove, ["splent_feature_admin"])
        assert result.exit_code == 0
        assert not link.is_symlink()
