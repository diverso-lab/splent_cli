"""Tests for the pre-flight guard service.

Hardened behavior under test: a crash inside the SAT checker or the contract
(compat) checker must NOT be swallowed and reported as a clean PASS.
``run_preflight`` must return False when an underlying check raises, while a
genuinely clean / satisfiable config returns True.

All real work (SAT solving, contract parsing, network) is mocked at the
boundary so the suite needs no docker / git / network / solver. The internal
check functions are imported inside ``run_preflight`` from their defining
modules, so we patch them at those source modules.
"""

from unittest.mock import patch

import pytest


# Patch targets — run_preflight does a late `from ... import` inside the body,
# which binds to the attributes on these source modules.
SAT = "splent_cli.commands.product.product_validate._run_sat_check"
COMPAT = "splent_cli.commands.product.product_validate._run_compat_check"
READ_APP = "splent_cli.commands.uvl.uvl_utils.read_splent_app"
LOAD_PYPROJECT = "splent_cli.commands.uvl.uvl_utils.load_pyproject"


def _make_product(tmp_path, *, app="test_app", features=None):
    """Create a minimal product dir with a pyproject.toml and return WORKING_DIR.

    `features` is a list placed under [tool.splent].features.
    """
    product_dir = tmp_path / app
    product_dir.mkdir(parents=True, exist_ok=True)
    pyproject = product_dir / "pyproject.toml"
    feats = features or []
    feats_toml = ", ".join(f'"{f}"' for f in feats)
    pyproject.write_text(f"[tool.splent]\nfeatures = [{feats_toml}]\n")
    return product_dir


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Set up WORKING_DIR + SPLENT_APP pointing at a real product dir."""
    _make_product(tmp_path, app="test_app")
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")
    # Avoid the .env fallback in read_splent_app reading the real workspace.
    monkeypatch.delenv("SPLENT_ENV", raising=False)
    return tmp_path


def _import_preflight():
    from splent_cli.services.preflight import run_preflight

    return run_preflight


# ---------------------------------------------------------------------------
# Hardened behavior: a checker crash must never read as a clean PASS.
# ---------------------------------------------------------------------------


def test_sat_checker_crash_is_not_reported_as_pass(env):
    """If the SAT checker raises, run_preflight must return False (not True)."""
    run_preflight = _import_preflight()

    with (
        patch(SAT, side_effect=RuntimeError("solver exploded")),
        patch(COMPAT, return_value=([], [], [])),
    ):
        result = run_preflight(interactive=False)

    assert result is False


def test_sat_checker_crash_surfaces_message_not_traceback(env):
    """The crash is reported cleanly in interactive output (no raw traceback)."""
    run_preflight = _import_preflight()

    import click

    @click.command()
    def cmd():
        ok = run_preflight(interactive=True)
        # Bubble the boolean out as exit code so we can assert on it too.
        raise SystemExit(0 if ok else 2)

    from click.testing import CliRunner

    with (
        patch(SAT, side_effect=RuntimeError("solver exploded")),
        patch(COMPAT, return_value=([], [], [])),
    ):
        res = CliRunner(mix_stderr=False).invoke(cmd, standalone_mode=False)

    # run_preflight returned False -> our cmd exits 2.
    assert res.exit_code == 2
    out = res.output + (res.stderr or "")
    assert "Traceback" not in out
    assert "crash" in out.lower()


def test_contract_checker_crash_is_not_reported_as_pass(env):
    """If the contract checker raises, run_preflight must return False.

    An empty ``errors`` list reads as a clean PASS; a crash must not be
    swallowed into that.
    """
    run_preflight = _import_preflight()

    with (
        patch(SAT, return_value=(True, [], None, None)),
        patch(COMPAT, side_effect=ValueError("contract parser exploded")),
    ):
        result = run_preflight(interactive=False)

    assert result is False


def test_contract_checker_crash_surfaces_clean_message(env):
    run_preflight = _import_preflight()

    import click
    from click.testing import CliRunner

    @click.command()
    def cmd():
        ok = run_preflight(interactive=True)
        raise SystemExit(0 if ok else 2)

    with (
        patch(SAT, return_value=(True, [], None, None)),
        patch(COMPAT, side_effect=ValueError("contract parser exploded")),
    ):
        res = CliRunner(mix_stderr=False).invoke(cmd, standalone_mode=False)

    assert res.exit_code == 2
    out = res.output + (res.stderr or "")
    assert "Traceback" not in out
    assert "contract" in out.lower() and "crash" in out.lower()


def test_both_checkers_crash_returns_false(env):
    run_preflight = _import_preflight()

    with (
        patch(SAT, side_effect=RuntimeError("boom")),
        patch(COMPAT, side_effect=RuntimeError("bang")),
    ):
        assert run_preflight(interactive=False) is False


# ---------------------------------------------------------------------------
# Happy path: a genuinely clean / satisfiable config returns True.
# ---------------------------------------------------------------------------


def test_clean_config_returns_true(env):
    """SAT satisfiable + no contract errors -> True."""
    run_preflight = _import_preflight()

    with (
        patch(SAT, return_value=(True, ["feat_a"], None, None)),
        patch(COMPAT, return_value=([], [], [])),
    ):
        assert run_preflight(interactive=False) is True


def test_clean_config_with_warnings_still_returns_true(env):
    """Contract warnings (but no errors) do not fail pre-flight."""
    run_preflight = _import_preflight()

    findings = []
    errors = []
    warnings = [{"field": "x", "message": "heads up"}]

    with (
        patch(SAT, return_value=(True, [], None, None)),
        patch(COMPAT, return_value=(findings, errors, warnings)),
    ):
        assert run_preflight(interactive=False) is True


def test_unsatisfiable_sat_returns_false(env):
    """A real (non-crash) unsatisfiable result is a normal failure -> False."""
    run_preflight = _import_preflight()

    with (
        patch(SAT, return_value=(False, [], None, None)),
        patch(COMPAT, return_value=([], [], [])),
    ):
        assert run_preflight(interactive=False) is False


def test_contract_errors_return_false(env):
    """Genuine contract errors -> False (and not a crash path)."""
    run_preflight = _import_preflight()

    errors = [{"field": "db", "message": "incompatible engines"}]

    with (
        patch(SAT, return_value=(True, [], None, None)),
        patch(COMPAT, return_value=([], errors, [])),
    ):
        assert run_preflight(interactive=False) is False


# ---------------------------------------------------------------------------
# Phase 0 sanity guards short-circuit before the checkers run.
# ---------------------------------------------------------------------------


def test_duplicate_features_fail_before_checkers(env, tmp_path):
    """A duplicate feature entry fails pre-flight without invoking SAT/contract."""
    # Rewrite pyproject with a duplicate bare feature name.
    pyproject = tmp_path / "test_app" / "pyproject.toml"
    pyproject.write_text(
        "[tool.splent]\n"
        'features = ["org/splent_feature_auth", "org/splent_feature_auth@1.0.0"]\n'
    )

    run_preflight = _import_preflight()

    with patch(SAT) as msat, patch(COMPAT) as mcompat:
        result = run_preflight(interactive=False)

    assert result is False
    # Short-circuited: the checkers were never reached.
    msat.assert_not_called()
    mcompat.assert_not_called()


def test_build_mode_checks_feature_readiness(env):
    """build_mode=True runs the extra feature-readiness phase.

    With a clean SAT/contract and no prod features, build mode still passes.
    """
    run_preflight = _import_preflight()

    with (
        patch(SAT, return_value=(True, [], None, None)),
        patch(COMPAT, return_value=([], [], [])),
    ):
        # No features_prod in pyproject -> _check_features_ready returns True.
        assert run_preflight(interactive=False, build_mode=True) is True
