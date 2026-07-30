"""Tests for the feature:attach command."""

import tomllib

import pytest
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
    """Asking to attach a version the cache does not have means wanting it.

    It used to print the clone command and exit, which made pinning a
    freshly released feature a two-step dance on every machine except the
    one that released it.
    """

    def _no_network(self, monkeypatch, on_call=None):
        """Stub the fetch. These are unit tests and must not clone."""
        import splent_cli.commands.feature.feature_attach as attach_module
        from splent_cli.commands.feature import feature_clone

        def fake_callback(full_name):
            if on_call:
                on_call(full_name)

        monkeypatch.setattr(feature_clone.feature_clone, "callback", fake_callback)
        return attach_module

    def test_it_fetches_the_missing_version(
        self, runner, product_workspace, monkeypatch
    ):
        asked = []
        self._no_network(monkeypatch, on_call=asked.append)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert asked == ["splent_io/auth@v1.0.0"]

    def test_it_says_it_is_fetching(self, runner, product_workspace, monkeypatch):
        self._no_network(monkeypatch)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "fetching it" in result.output

    def test_it_fails_when_the_fetch_brings_nothing(
        self, runner, product_workspace, monkeypatch
    ):
        """A fetch that leaves the cache empty has nothing to attach."""
        self._no_network(monkeypatch)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert result.exit_code == 1
        assert "nothing to attach" in result.output

    def test_it_continues_when_the_fetch_works(
        self, runner, product_workspace, monkeypatch
    ):
        def populate(full_name):
            (
                product_workspace
                / ".splent_cache"
                / "features"
                / "splent_io"
                / "auth@v1.0.0"
            ).mkdir(parents=True)

        self._no_network(monkeypatch, on_call=populate)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "nothing to attach" not in result.output


# ---------------------------------------------------------------------------
# Successful attach
# ---------------------------------------------------------------------------


class TestSuccessfulAttach:
    def _setup_cache(
        self, workspace, namespace="splent_io", name="auth", version="v1.0.0"
    ):
        cache_dir = (
            workspace / ".splent_cache" / "features" / namespace / f"{name}@{version}"
        )
        cache_dir.mkdir(parents=True)
        return cache_dir

    def test_reports_attached(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "attached" in result.output.lower() or "done" in result.output.lower()

    def test_updates_pyproject(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        content = (product_workspace / "test_app" / "pyproject.toml").read_text()
        assert "auth" in content

    def test_creates_symlink(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        link = product_workspace / "test_app" / "features" / "splent_io" / "auth@v1.0.0"
        assert link.is_symlink()

    def test_success_message(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert "done" in result.output.lower() or "attached" in result.output.lower()

    def test_idempotent_already_present(self, runner, product_workspace):
        self._setup_cache(product_workspace)
        runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        result = runner.invoke(feature_attach, ["splent_io/auth", "v1.0.0"])
        assert result.exit_code == 0
        assert "already" in result.output.lower()

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
    def _setup_cache(
        self, workspace, namespace="splent_io", name="auth", version="v1.0.0"
    ):
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


# ---------------------------------------------------------------------------
# Namespace spelling: splent-io (GitHub org) and splent_io (Python namespace)
# are the same namespace, so attaching must never declare the feature twice.
# ---------------------------------------------------------------------------


class TestNamespaceSpelling:
    def _setup_cache(self, workspace, name="theme", version="v0.2.1"):
        cache_dir = (
            workspace / ".splent_cache" / "features" / "splent_io" / f"{name}@{version}"
        )
        cache_dir.mkdir(parents=True)
        return cache_dir

    def _write_pyproject(self, workspace, features=None, dev=None, prod=None):
        splent = {}
        for key, value in (
            ("features", features),
            ("features_dev", dev),
            ("features_prod", prod),
        ):
            if value is not None:
                splent[key] = "[" + ", ".join(f'"{v}"' for v in value) + "]"
        body = '[project]\nname = "test_app"\nversion = "1.0.0"\n[tool.splent]\n'
        body += "".join(f"{k} = {v}\n" for k, v in splent.items())
        path = workspace / "test_app" / "pyproject.toml"
        path.write_text(body)
        return path

    def _lists(self, pyproject):
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data.get("tool", {}).get("splent", {})

    def test_dash_entry_is_replaced_not_duplicated(self, runner, product_workspace):
        """One declaration, in the spelling the product already used.

        splent-io and splent_io are the same namespace, so typing the other
        one must not add a second entry. It does not change the existing
        spelling either: a product carrying both reads as if it depended on
        two orgs, and product:validate warns about exactly that.
        """
        pyproject = self._write_pyproject(
            product_workspace, features=["splent-io/theme"]
        )
        self._setup_cache(product_workspace)

        result = runner.invoke(feature_attach, ["splent_io/theme", "v0.2.1"])
        assert result.exit_code == 0

        lists = self._lists(pyproject)
        assert lists["features"] == ["splent-io/theme@v0.2.1"]

    def test_replaces_entry_declared_in_another_list(self, runner, product_workspace):
        pyproject = self._write_pyproject(
            product_workspace, features=[], dev=["splent-io/theme@v0.1.0"]
        )
        self._setup_cache(product_workspace)

        result = runner.invoke(feature_attach, ["splent_io/theme", "v0.2.1"])
        assert result.exit_code == 0

        lists = self._lists(pyproject)
        assert lists["features"] == ["splent-io/theme@v0.2.1"]
        assert lists["features_dev"] == []

    def test_a_product_with_no_entry_yet_keeps_what_was_typed(
        self, runner, product_workspace
    ):
        """There is nothing to be consistent with, so the argument decides."""
        pyproject = self._write_pyproject(product_workspace, features=[])
        self._setup_cache(product_workspace)

        result = runner.invoke(feature_attach, ["splent_io/theme", "v0.2.1"])
        assert result.exit_code == 0

        assert self._lists(pyproject)["features"] == ["splent_io/theme@v0.2.1"]

    def test_the_spelling_comes_from_any_list_not_just_this_one(
        self, runner, product_workspace
    ):
        pyproject = self._write_pyproject(
            product_workspace, features=[], dev=["splent-io/auth@v1.7.0"]
        )
        self._setup_cache(product_workspace)

        result = runner.invoke(feature_attach, ["splent_io/theme", "v0.2.1"])
        assert result.exit_code == 0

        assert self._lists(pyproject)["features"] == ["splent-io/theme@v0.2.1"]

    def test_reports_the_replaced_entry(self, runner, product_workspace):
        self._write_pyproject(product_workspace, features=["splent-io/theme"])
        self._setup_cache(product_workspace)

        result = runner.invoke(feature_attach, ["splent_io/theme", "v0.2.1"])
        assert "replacing splent-io/theme" in result.output

    def test_stale_symlink_is_cleaned_up(self, runner, product_workspace):
        self._write_pyproject(product_workspace, features=["splent-io/theme"])
        self._setup_cache(product_workspace)

        link_dir = product_workspace / "test_app" / "features" / "splent_io"
        link_dir.mkdir(parents=True)
        target = product_workspace / "theme"
        target.mkdir()
        stale_link = link_dir / "theme"
        stale_link.symlink_to(target)

        runner.invoke(feature_attach, ["splent_io/theme", "v0.2.1"])

        assert not stale_link.is_symlink()
        assert (link_dir / "theme@v0.2.1").is_symlink()

    def test_other_namespace_is_left_alone(self, runner, product_workspace):
        pyproject = self._write_pyproject(
            product_workspace, features=["drorganvidez/theme"]
        )
        self._setup_cache(product_workspace)

        runner.invoke(feature_attach, ["splent_io/theme", "v0.2.1"])

        lists = self._lists(pyproject)
        assert "drorganvidez/theme" in lists["features"]
        assert "splent_io/theme@v0.2.1" in lists["features"]
