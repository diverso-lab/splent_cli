"""Tests for the pre-release lint+tests gate in services/release.py.

The gate must (1) run lint then tests for the released entity, (2) abort on
either failure, (3) be skippable, and — most importantly — (4) run BEFORE any
irreversible step (version bump / commit / tag push / PyPI upload) so a failure
leaves the repo and remotes untouched.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from splent_cli.services import release


def _cp(returncode):
    return SimpleNamespace(returncode=returncode, stdout="", stderr="")


class TestRunPreReleaseChecks:
    def test_passes_when_lint_and_tests_ok(self):
        with (
            patch.object(release, "_lint_path", return_value=True),
            patch.object(release, "_test_entity", return_value=True),
        ):
            release.run_pre_release_checks("cli", "splent_cli", "/x")

    def test_aborts_when_lint_fails_and_skips_tests(self):
        with (
            patch.object(release, "_lint_path", return_value=False),
            patch.object(release, "_test_entity", return_value=True) as tests,
        ):
            with pytest.raises(SystemExit):
                release.run_pre_release_checks("cli", "splent_cli", "/x")
            tests.assert_not_called()

    def test_aborts_when_tests_fail(self):
        with (
            patch.object(release, "_lint_path", return_value=True),
            patch.object(release, "_test_entity", return_value=False),
        ):
            with pytest.raises(SystemExit):
                release.run_pre_release_checks("feature", "ns/f", "/x")


class TestTestEntityDispatch:
    def test_cli_runs_pytest_in_package_dir(self):
        with patch.object(release, "run", return_value=_cp(0)) as r:
            assert release._test_entity("cli", "splent_cli", "/pkg") is True
        cmd = r.call_args[0][0]
        assert cmd[1:] == ["-m", "pytest", "tests", "-q"]
        assert r.call_args[1]["cwd"] == "/pkg"

    def test_framework_runs_pytest_in_package_dir(self):
        with patch.object(release, "run", return_value=_cp(0)) as r:
            release._test_entity("framework", "splent_framework", "/fw")
        assert r.call_args[1]["cwd"] == "/fw"

    def test_feature_runs_feature_test_with_bare_name(self):
        with patch.object(release, "run", return_value=_cp(0)) as r:
            release._test_entity("feature", "splent_io/splent_feature_auth", "/x")
        cmd = r.call_args[0][0]
        assert cmd[1:] == [
            "-m",
            "splent_cli",
            "feature:test",
            "splent_feature_auth",
        ]

    def test_product_runs_product_test(self):
        with patch.object(release, "run", return_value=_cp(0)) as r:
            release._test_entity("product", "myapp", "/x")
        assert r.call_args[0][0][1:] == ["-m", "splent_cli", "product:test"]

    def test_nonzero_returncode_is_failure(self):
        with patch.object(release, "run", return_value=_cp(1)):
            assert release._test_entity("cli", "x", "/p") is False


class TestPipelineGateOrdering:
    """The gate runs before any irreversible step, and --skip-checks bypasses."""

    def test_gate_failure_blocks_all_state_changes(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        with (
            patch.object(release, "validate_release_env"),
            patch.object(release, "get_repo_from_path", return_value="org/repo"),
            patch.object(
                release,
                "run_pre_release_checks",
                side_effect=SystemExit(1),
            ) as gate,
            patch.object(release, "update_version") as upd,
            patch.object(release, "commit_and_push") as commit,
            patch.object(release, "create_and_push_tag") as tag,
            patch.object(release, "build_and_upload_pypi") as pypi,
        ):
            with pytest.raises(SystemExit):
                release.run_release_pipeline("x", str(tmp_path), "1.0.0", kind="cli")
            gate.assert_called_once()
            upd.assert_not_called()
            commit.assert_not_called()
            tag.assert_not_called()
            pypi.assert_not_called()

    def test_skip_checks_bypasses_gate(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        with (
            patch.object(release, "validate_release_env"),
            patch.object(release, "get_repo_from_path", return_value="org/repo"),
            patch.object(release, "run_pre_release_checks") as gate,
            patch.object(release, "update_version") as upd,
            patch.object(release, "commit_and_push"),
            patch.object(release, "create_and_push_tag"),
            patch.object(release, "create_github_release"),
            patch.object(release, "build_and_upload_pypi"),
            patch("splent_cli.commands.clear.clear_build.clean_build_artifacts"),
        ):
            release.run_release_pipeline(
                "x", str(tmp_path), "1.0.0", kind="cli", skip_checks=True
            )
            gate.assert_not_called()
            upd.assert_called_once()


class TestLintPathReal:
    """Real ruff invocation (ruff is a dev dependency)."""

    def test_clean_file_passes(self, tmp_path):
        (tmp_path / "ok.py").write_text("x = 1\nprint(x)\n")
        assert release._lint_path(str(tmp_path)) is True

    def test_unformatted_file_fails(self, tmp_path):
        # Bad import order + formatting ruff will object to.
        (tmp_path / "bad.py").write_text("import os,sys\nx=1\n")
        assert release._lint_path(str(tmp_path)) is False
