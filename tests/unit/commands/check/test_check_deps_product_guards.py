"""Regression net for check:deps and check:product diagnostics.

These two commands are *diagnostics*: they must degrade gracefully when a
tool / file / network dependency is missing — reporting FAIL / WARN cleanly
without ever leaking a Python traceback. They must also surface real
problems (missing UVL, dependency violations, missing env vars, broken
symlinks) with a non-zero exit code.

All boundaries are mocked: no real docker / git / network / database.
The app-boot helper (get_app) is intentionally *not* available in the test
environment, which exercises the graceful-degradation branches directly.
"""

import pytest
from click.testing import CliRunner

from splent_cli.commands.check.check_deps import check_deps
from splent_cli.commands.check.check_product import check_product


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _clean(result):
    """No raw exception ever reaches the user."""
    combined = result.output + result.stderr
    assert "Traceback" not in combined
    assert "CalledProcessError" not in combined
    assert "TOMLDecodeError" not in combined


def _set_features(product_dir, *entries):
    """Write a pyproject.toml declaring the given feature entries."""
    feats = ", ".join(f'"{e}"' for e in entries)
    (product_dir / "pyproject.toml").write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n'
        f"[tool.splent]\nfeatures = [{feats}]\n"
    )


def _write_uvl(product_dir, uvl_filename, text):
    uvl_dir = product_dir / "uvl"
    uvl_dir.mkdir(parents=True, exist_ok=True)
    (uvl_dir / uvl_filename).write_text(text)


# ===========================================================================
# check:deps — hardened / graceful-degradation behaviors
# ===========================================================================


class TestCheckDepsGuards:
    def test_no_splent_app_exits_clean(self, runner, workspace):
        # SPLENT_APP unset (workspace fixture deletes it).
        result = runner.invoke(check_deps)
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output
        _clean(result)

    def test_missing_pyproject_reports_fail_no_traceback(
        self, runner, workspace, monkeypatch
    ):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        # Product dir does not even exist → PyprojectReader raises
        # FileNotFoundError, which must be caught and reported cleanly.
        result = runner.invoke(check_deps)
        assert result.exit_code == 1
        assert "Cannot read pyproject.toml" in result.output
        _clean(result)

    def test_no_uvl_configured_reports_fail(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        product_dir = workspace / "test_app"
        product_dir.mkdir()
        # Valid pyproject but no [tool.splent].spl and no [tool.splent.uvl].file
        (product_dir / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n[tool.splent]\n'
        )
        result = runner.invoke(check_deps)
        assert result.exit_code == 1
        assert "No UVL configured" in result.output
        _clean(result)

    def test_uvl_file_missing_reports_fail(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        product_dir = workspace / "test_app"
        product_dir.mkdir()
        # Legacy UVL config pointing at a file that does not exist on disk.
        (product_dir / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[tool.splent.uvl]\nfile = "model.uvl"\n'
        )
        result = runner.invoke(check_deps)
        assert result.exit_code == 1
        assert "UVL file not found" in result.output
        _clean(result)


# ===========================================================================
# check:deps — core happy-path / violation behaviors
# ===========================================================================


def _legacy_uvl_text():
    """UVL declaring two features where profile is allowed to import auth."""
    return (
        "features\n"
        "    profile {package 'splent_feature_profile'}\n"
        "    auth {package 'splent_feature_auth'}\n"
        "constraints\n"
        "    profile => auth\n"
    )


def _make_feature_src(workspace, pkg_name, py_content):
    """Create a feature directory at <workspace>/<product>/features/<org>/<pkg>
    with src/<org>/<pkg>/code.py containing py_content.

    _resolve_feature_paths keys by the entry name (split on '@'), and
    _scan_feature_imports walks src/<org>/<feature_name>/.
    """
    org = "splent_io"
    feat_root = workspace / "test_app" / "features" / org / pkg_name
    src = feat_root / "src" / org / pkg_name
    src.mkdir(parents=True)
    (src / "code.py").write_text(py_content)
    return feat_root


class TestCheckDepsBehavior:
    def test_allowed_dependency_passes(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        product_dir = workspace / "test_app"
        product_dir.mkdir()
        _set_features(product_dir)
        _write_uvl(product_dir, "model.uvl", _legacy_uvl_text())
        (product_dir / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[tool.splent.uvl]\nfile = "model.uvl"\n'
        )
        # profile imports auth — allowed by "profile => auth".
        _make_feature_src(
            workspace,
            "splent_feature_profile",
            "from splent_io.splent_feature_auth import something\n",
        )
        _make_feature_src(workspace, "splent_feature_auth", "x = 1\n")
        result = runner.invoke(check_deps)
        assert result.exit_code == 0
        assert "consistent with UVL" in result.output
        _clean(result)

    def test_inverted_dependency_fails(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        product_dir = workspace / "test_app"
        product_dir.mkdir()
        _write_uvl(product_dir, "model.uvl", _legacy_uvl_text())
        (product_dir / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[tool.splent.uvl]\nfile = "model.uvl"\n'
        )
        # auth imports profile — but UVL says profile => auth (inverted).
        _make_feature_src(
            workspace,
            "splent_feature_auth",
            "from splent_io.splent_feature_profile import x\n",
        )
        _make_feature_src(workspace, "splent_feature_profile", "y = 1\n")
        result = runner.invoke(check_deps)
        assert result.exit_code == 1
        assert "violation" in result.output.lower()
        assert "INVERTED" in result.output
        _clean(result)


# ===========================================================================
# check:product — hardened / graceful-degradation behaviors
# ===========================================================================


class TestCheckProductGuards:
    def test_no_product_selected_exits_clean(self, runner, workspace):
        # SPLENT_APP unset → requires_product decorator aborts cleanly.
        result = runner.invoke(check_product)
        assert result.exit_code == 1
        assert "No product selected" in result.output
        _clean(result)

    def test_missing_pyproject_reports_fail(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        (workspace / "test_app").mkdir()  # no pyproject.toml
        result = runner.invoke(check_product)
        assert result.exit_code == 1
        assert "pyproject.toml not found" in result.output
        _clean(result)

    def test_no_features_declared_exits_ok(self, runner, product_workspace):
        # product_workspace fixture writes an empty features list.
        result = runner.invoke(check_product)
        assert result.exit_code == 0
        assert "No features declared" in result.output
        _clean(result)

    def test_app_boot_failure_degrades_to_warnings(
        self, runner, product_workspace, monkeypatch
    ):
        # A feature is declared but no docker/.env, no symlinks, and the app
        # cannot boot in this environment. Every section must degrade to a
        # WARN / informational line — never a traceback — and since nothing
        # FAILs the command exits 0.
        product_dir = product_workspace / "test_app"
        _set_features(product_dir, "splent_io/splent_feature_auth@v1.0.0")
        # Remove docker dir so docker/.env is absent.
        result = runner.invoke(check_product)
        assert result.exit_code == 0
        # Graceful messages, not exceptions.
        assert "No docker/.env found" in result.output
        assert "skipping" in result.output  # config/blueprint sections
        _clean(result)


# ===========================================================================
# check:product — core failure-surfacing behaviors
# ===========================================================================


class TestCheckProductBehavior:
    def test_broken_symlink_is_reported_as_fail(
        self, runner, product_workspace, monkeypatch
    ):
        product_dir = product_workspace / "test_app"
        _set_features(product_dir, "splent_io/splent_feature_auth@v1.0.0")

        # Create a broken symlink for the declared feature.
        features_dir = product_dir / "features" / "splent_io"
        features_dir.mkdir(parents=True)
        link = features_dir / "splent_feature_auth@v1.0.0"
        link.symlink_to(product_dir / "does_not_exist")

        result = runner.invoke(check_product)
        assert result.exit_code == 1
        assert "broken" in result.output
        _clean(result)

    def test_missing_required_env_var_is_reported_as_fail(
        self, runner, product_workspace, monkeypatch
    ):
        product_dir = product_workspace / "test_app"
        _set_features(product_dir, "splent_io/splent_feature_auth@v1.0.0")

        # docker/.env exists (so the section runs) but lacks the required var.
        docker_dir = product_dir / "docker"
        docker_dir.mkdir(parents=True, exist_ok=True)
        (docker_dir / ".env").write_text("SOME_OTHER_VAR=1\n")

        # Editable feature pyproject at workspace root declares a required env
        # var via the contract; _resolve_feature_pyproject picks it up.
        feat_root = product_workspace / "splent_feature_auth"
        feat_root.mkdir(parents=True)
        (feat_root / "pyproject.toml").write_text(
            '[project]\nname = "splent_feature_auth"\nversion = "1.0.0"\n'
            "[tool.splent.contract.requires]\n"
            'env_vars = ["AUTH_SECRET_KEY"]\n'
        )

        result = runner.invoke(check_product)
        assert result.exit_code == 1
        assert "AUTH_SECRET_KEY" in result.output
        assert "missing" in result.output
        _clean(result)
