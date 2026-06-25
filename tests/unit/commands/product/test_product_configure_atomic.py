"""Tests for product:configure — hardened "Apply" stage.

These exercise ONLY the hardened write path of the (large, Flamapy-coupled)
configurator:

  * pyproject.toml is written ATOMICALLY with a backup (the new feature lists
    persist, the file stays valid TOML, no corruption, a .bak is left behind);
  * when feature version resolution fails (neither cache nor
    ``pip index versions`` yields a version) the command WARNS rather than
    silently pinning an unversioned entry.

The interactive walk and all Flamapy coupling are mocked at the module
boundary so no network / pip / flamapy / filesystem-catalog is needed:

  * ``_load_spl_model``      → returns a hand-built ``SPLModel``;
  * ``_configure_subtree``   → no-op (no prompting during the walk);
  * ``propagate``            → identity (selected stays as-is);
  * flamapy validation imports (``_require_flamapy``, ``FLAMAFeatureModel``,
    ``list_all_features_from_uvl``, ``write_csvconf_full``) are patched on
    ``splent_cli.commands.uvl.uvl_utils`` because the command imports them
    locally at call time;
  * ``splent_cli.utils.proc.run`` (the ``pip index versions`` shell-out) is
    patched — the command imports ``run`` locally, so patching the source
    module attribute takes effect.
"""
import os
import tempfile
import tomllib
from unittest.mock import MagicMock

import pytest
import tomli_w
from click.testing import CliRunner

import splent_cli.commands.product.product_configure as pc
from splent_cli.commands.product.product_configure import (
    SPLFeature,
    SPLModel,
    product_configure,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_model():
    """A minimal SPL model: root + one concrete (deployable) feature."""
    root = SPLFeature(name="MyProduct", package="")  # abstract root
    auth = SPLFeature(
        name="Auth",
        org="splent-io",
        package="splent_feature_auth",
        parent="MyProduct",
    )
    model = SPLModel(root_name="MyProduct", uvl_path="/fake/MySpl.uvl")
    model.features = {"MyProduct": root, "Auth": auth}
    return model


def _setup_product(tmp_path):
    """Create a workspace with a product pyproject.toml that has an SPL set."""
    workspace = tmp_path
    product = "test_app"
    product_path = workspace / product
    product_path.mkdir(parents=True)
    pyproject = product_path / "pyproject.toml"
    data = {
        "project": {"name": "test_app", "version": "0.1.0"},
        "tool": {"splent": {"spl": "MySpl", "features": ["stale/old@v0.0.1"]}},
    }
    pyproject.write_bytes(tomli_w.dumps(data).encode())
    return workspace, product_path, pyproject


def _patch_flow(monkeypatch, model, *, selected, pip_run):
    """Patch out every heavyweight/flamapy boundary, leaving the apply stage real.

    Returns nothing; installs all patches via monkeypatch.
    """
    monkeypatch.setattr(pc, "_load_spl_model", lambda catalog_dir, spl: model)
    monkeypatch.setattr(
        pc, "_configure_subtree", lambda *a, **k: None
    )
    # propagate(selected, model, mandatory, excluded=None) -> (selected, excluded)
    monkeypatch.setattr(
        pc,
        "propagate",
        lambda sel, m, mand, excl=None: (set(selected), set()),
    )
    monkeypatch.setattr(pc, "check_excludes", lambda sel, m: [])

    # Flamapy validation path — imported locally from uvl_utils.
    import splent_cli.commands.uvl.uvl_utils as uvl_utils

    monkeypatch.setattr(uvl_utils, "_require_flamapy", lambda: None)
    monkeypatch.setattr(
        uvl_utils,
        "list_all_features_from_uvl",
        lambda path: (["Auth"], "MyProduct"),
    )
    # Return a REAL throwaway file path: the command does os.remove() on it.
    def _fake_csvconf(universe, sel):
        fd, p = tempfile.mkstemp(suffix=".csvconf")
        os.close(fd)
        return p

    monkeypatch.setattr(uvl_utils, "write_csvconf_full", _fake_csvconf)
    # FLAMAFeatureModel is imported by the command directly from flamapy's
    # module, so patch it at the source.
    import flamapy.interfaces.python.flamapy_feature_model as ffm

    fake_fma = MagicMock()
    fake_fma.satisfiable_configuration.return_value = True
    monkeypatch.setattr(
        ffm, "FLAMAFeatureModel", lambda path: fake_fma
    )

    # The pip index versions shell-out, imported locally from proc.
    monkeypatch.setattr("splent_cli.utils.proc.run", pip_run)


# ---------------------------------------------------------------------------
# Hardened behavior 1: atomic write + backup
# ---------------------------------------------------------------------------


class TestAtomicWriteWithBackup:
    def test_pyproject_mutation_persists_and_stays_valid_toml(
        self, tmp_path, monkeypatch, runner
    ):
        workspace, product_path, pyproject = _setup_product(tmp_path)
        monkeypatch.setenv("WORKING_DIR", str(workspace))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        model = _make_model()
        # Cache contains a version for the feature, so no pip resolution needed.
        cache = workspace / ".splent_cache" / "features" / "splent_io"
        (cache / "splent_feature_auth@v1.2.3").mkdir(parents=True)

        pip_run = MagicMock()  # must NOT be called when cache resolves it
        _patch_flow(monkeypatch, model, selected={"Auth"}, pip_run=pip_run)

        result = runner.invoke(product_configure, input="y\n")

        assert result.exit_code == 0, result.output
        # File still parses as valid TOML.
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        # The mutation persisted: the new versioned entry replaced the stale one.
        features = data["tool"]["splent"]["features"]
        assert "splent-io/splent_feature_auth@v1.2.3" in features
        assert "stale/old@v0.0.1" not in features
        # No partial/temp file left in the product dir.
        leftovers = [
            n for n in os.listdir(product_path) if n.endswith(".tmp")
        ]
        assert leftovers == []

    def test_backup_file_created_with_original_content(
        self, tmp_path, monkeypatch, runner
    ):
        workspace, product_path, pyproject = _setup_product(tmp_path)
        monkeypatch.setenv("WORKING_DIR", str(workspace))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        original_bytes = pyproject.read_bytes()

        model = _make_model()
        cache = workspace / ".splent_cache" / "features" / "splent_io"
        (cache / "splent_feature_auth@v2.0.0").mkdir(parents=True)

        pip_run = MagicMock()
        _patch_flow(monkeypatch, model, selected={"Auth"}, pip_run=pip_run)

        result = runner.invoke(product_configure, input="y\n")

        assert result.exit_code == 0, result.output
        bak = product_path / "pyproject.toml.bak"
        assert bak.is_file()
        # Backup holds the ORIGINAL (pre-write) content.
        assert bak.read_bytes() == original_bytes

    def test_cancel_leaves_pyproject_untouched(
        self, tmp_path, monkeypatch, runner
    ):
        workspace, product_path, pyproject = _setup_product(tmp_path)
        monkeypatch.setenv("WORKING_DIR", str(workspace))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        original_bytes = pyproject.read_bytes()

        model = _make_model()
        cache = workspace / ".splent_cache" / "features" / "splent_io"
        (cache / "splent_feature_auth@v1.0.0").mkdir(parents=True)

        pip_run = MagicMock()
        _patch_flow(monkeypatch, model, selected={"Auth"}, pip_run=pip_run)

        # Answer "n" to the apply confirmation.
        result = runner.invoke(product_configure, input="n\n")

        assert result.exit_code == 0, result.output
        assert "Cancelled" in result.output
        # Nothing written, no backup created.
        assert pyproject.read_bytes() == original_bytes
        assert not (product_path / "pyproject.toml.bak").exists()


# ---------------------------------------------------------------------------
# Hardened behavior 2: unresolved version → WARN (not silent unversioned pin)
# ---------------------------------------------------------------------------


class TestVersionResolutionFailureWarns:
    def test_warns_when_pip_index_returns_nothing(
        self, tmp_path, monkeypatch, runner
    ):
        workspace, product_path, pyproject = _setup_product(tmp_path)
        monkeypatch.setenv("WORKING_DIR", str(workspace))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        model = _make_model()
        # No cache dir at all → must fall through to pip, which returns nothing.
        pip_run = MagicMock(
            return_value=MagicMock(returncode=1, stdout="", stderr="")
        )
        _patch_flow(monkeypatch, model, selected={"Auth"}, pip_run=pip_run)

        result = runner.invoke(product_configure, input="y\n")

        assert result.exit_code == 0, result.output
        # It surfaced a warning rather than silently pinning unversioned.
        out = result.output.lower()
        assert "warning" in out
        assert "could not resolve a version" in out
        assert "splent-io/splent_feature_auth" in result.output
        # No traceback leaked.
        assert "Traceback" not in result.output

    def test_pip_used_only_when_cache_misses(
        self, tmp_path, monkeypatch, runner
    ):
        workspace, product_path, pyproject = _setup_product(tmp_path)
        monkeypatch.setenv("WORKING_DIR", str(workspace))
        monkeypatch.setenv("SPLENT_APP", "test_app")

        model = _make_model()
        pip_run = MagicMock(
            return_value=MagicMock(
                returncode=0,
                stdout="splent_feature_auth (3.4.5)\nAvailable versions: 3.4.5",
                stderr="",
            )
        )
        _patch_flow(monkeypatch, model, selected={"Auth"}, pip_run=pip_run)

        result = runner.invoke(product_configure, input="y\n")

        assert result.exit_code == 0, result.output
        pip_run.assert_called_once()
        # The version parsed from pip output was applied (prefixed with "v").
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        assert (
            "splent-io/splent_feature_auth@v3.4.5"
            in data["tool"]["splent"]["features"]
        )


# ---------------------------------------------------------------------------
# Core happy-path: guards before the apply stage
# ---------------------------------------------------------------------------


class TestPreApplyGuards:
    def test_missing_product_pyproject_clean_error(
        self, tmp_path, monkeypatch, runner
    ):
        # Workspace exists but the product dir / pyproject does not.
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "ghost_app")
        result = runner.invoke(product_configure)
        assert result.exit_code != 0
        assert "Product not found" in result.stderr
        assert "Traceback" not in result.stderr

    def test_no_spl_configured_clean_error(
        self, tmp_path, monkeypatch, runner
    ):
        product_path = tmp_path / "test_app"
        product_path.mkdir()
        (product_path / "pyproject.toml").write_bytes(
            tomli_w.dumps(
                {"project": {"name": "test_app"}, "tool": {"splent": {}}}
            ).encode()
        )
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        result = runner.invoke(product_configure)
        assert result.exit_code != 0
        assert "No SPL configured" in result.stderr
        assert "Traceback" not in result.stderr
