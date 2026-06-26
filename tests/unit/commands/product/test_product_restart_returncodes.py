"""Tests for product:restart — failing sub-steps are SURFACED, not hidden.

Hardened behaviors under test:
- A non-zero "pip install -e" returncode (feature install failure) aborts the
  restart with exit code 1 instead of charging ahead and printing success.
- A non-zero app-start returncode is surfaced (exit 1, "Failed to start"),
  and the green "done." success line is NOT printed.
- The happy path (everything returns 0) prints "done." and exits 0.

All docker/git/process boundaries are mocked:
- ``compose.find_main_container`` → fake container id (no docker needed).
- ``product_resolve.product_sync`` → no-op command (no symlink/filesystem work).
- the proc helper ``run`` (pip install + app start) and the directly-imported
  ``subprocess.run`` (feature restarts + pkill + pip list) are stubbed.
"""

import click
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_restart import product_restart


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# A no-op replacement for the product_sync command that ctx.invoke() calls.
@click.command("product:sync")
@click.option("--force", is_flag=True)
@click.option("--yes", is_flag=True)
def _noop_sync(force, yes):  # pragma: no cover - body never matters
    return None


def _make_pyproject_with_feature(tmp_path, entry):
    """Rewrite test_app's pyproject.toml to declare a single feature."""
    pyproject = tmp_path / "test_app" / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n'
        "[project.optional-dependencies]\n"
        f'features = ["{entry}"]\n'
    )


def _patch_boundaries(detect_return):
    """Context-manager bundle patching every external boundary.

    Returns a tuple of (mock_run, mock_subprocess_run) via the caller using
    the returned patchers; here we just return the started patch objects.
    """
    patchers = [
        patch(
            "splent_cli.commands.product.product_restart.compose.find_main_container",
            return_value="fake_container_id",
        ),
        patch(
            "splent_cli.commands.product.product_resolve.product_sync",
            _noop_sync,
        ),
        patch(
            "splent_cli.commands.product.product_restart._detect_changes",
            return_value=detect_return,
        ),
    ]
    return patchers


# ---------------------------------------------------------------------------
# Hardened: failing pip install -e returncode aborts the restart
# ---------------------------------------------------------------------------


class TestFeatureInstallFailureSurfaced:
    def test_failing_pip_install_aborts_with_exit_1(self, runner, product_workspace):
        """One feature whose `pip install -e` returns non-zero → abort."""
        to_install = [("splent_feature_auth", "/workspace/splent_feature_auth", True)]

        def fake_run(cmd, *a, **k):
            # proc.run is used for pip install (and would be for app start).
            # Make the pip install step fail.
            if "pip" in cmd and "install" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="boom: pip failed")
            return MagicMock(returncode=0, stdout="", stderr="")

        patchers = _patch_boundaries(to_install)
        for p in patchers:
            p.start()
        try:
            with (
                patch(
                    "splent_cli.commands.product.product_restart.run",
                    side_effect=fake_run,
                ),
                patch(
                    "splent_cli.commands.product.product_restart.subprocess.run",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                ),
            ):
                result = runner.invoke(product_restart, ["--dev"])
        finally:
            for p in patchers:
                p.stop()

        assert result.exit_code == 1
        # The success line must NOT appear when an install failed.
        assert "done." not in result.output
        # A clean, surfaced error — not a traceback.
        assert "failed to install" in result.output.lower()
        assert "Traceback" not in result.output
        assert "CalledProcessError" not in result.output

    def test_failing_pip_install_never_reaches_app_start(
        self, runner, product_workspace
    ):
        """If install fails, the app-start `docker exec -d ... bash` is never run."""
        to_install = [("splent_feature_auth", "/workspace/splent_feature_auth", True)]

        start_calls = []

        def fake_run(cmd, *a, **k):
            if "pip" in cmd and "install" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="boom")
            if "-d" in cmd:  # the detached app-start exec
                start_calls.append(cmd)
            return MagicMock(returncode=0, stdout="", stderr="")

        patchers = _patch_boundaries(to_install)
        for p in patchers:
            p.start()
        try:
            with (
                patch(
                    "splent_cli.commands.product.product_restart.run",
                    side_effect=fake_run,
                ),
                patch(
                    "splent_cli.commands.product.product_restart.subprocess.run",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                ),
            ):
                result = runner.invoke(product_restart, ["--dev"])
        finally:
            for p in patchers:
                p.stop()

        assert result.exit_code == 1
        assert start_calls == [], "app start must not run after an install failure"


# ---------------------------------------------------------------------------
# Hardened: failing app-start returncode is surfaced
# ---------------------------------------------------------------------------


class TestAppStartFailureSurfaced:
    def test_failing_app_start_exits_1_and_no_success(self, runner, product_workspace):
        """No features to install, but the detached app-start exits non-zero."""

        def fake_run(cmd, *a, **k):
            if "-d" in cmd:  # detached app-start exec
                return MagicMock(returncode=2, stdout="", stderr="no such container")
            return MagicMock(returncode=0, stdout="", stderr="")

        patchers = _patch_boundaries([])  # nothing to install
        for p in patchers:
            p.start()
        try:
            with (
                patch(
                    "splent_cli.commands.product.product_restart.run",
                    side_effect=fake_run,
                ),
                patch(
                    "splent_cli.commands.product.product_restart.subprocess.run",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                ),
            ):
                result = runner.invoke(product_restart, ["--dev"])
        finally:
            for p in patchers:
                p.stop()

        assert result.exit_code == 1
        assert "Failed to start" in result.output
        assert "done." not in result.output
        assert "Traceback" not in result.output

    def test_full_app_start_failure_also_surfaced(self, runner, product_workspace):
        """--full path runs the entrypoint; a non-zero start is still surfaced."""

        def fake_run(cmd, *a, **k):
            if "-d" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="exec failed")
            return MagicMock(returncode=0, stdout="", stderr="")

        patchers = _patch_boundaries([])
        for p in patchers:
            p.start()
        try:
            with (
                patch(
                    "splent_cli.commands.product.product_restart.run",
                    side_effect=fake_run,
                ),
                patch(
                    "splent_cli.commands.product.product_restart.subprocess.run",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                ),
            ):
                result = runner.invoke(product_restart, ["--full", "--dev"])
        finally:
            for p in patchers:
                p.stop()

        assert result.exit_code == 1
        assert "Failed to start" in result.output
        assert "done." not in result.output


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_no_changes_restart_succeeds(self, runner, product_workspace):
        """Nothing to install + app starts cleanly → exit 0, green done."""

        def fake_run(cmd, *a, **k):
            return MagicMock(returncode=0, stdout="", stderr="")

        patchers = _patch_boundaries([])
        for p in patchers:
            p.start()
        try:
            with (
                patch(
                    "splent_cli.commands.product.product_restart.run",
                    side_effect=fake_run,
                ),
                patch(
                    "splent_cli.commands.product.product_restart.subprocess.run",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                ),
            ):
                result = runner.invoke(product_restart, ["--dev"])
        finally:
            for p in patchers:
                p.stop()

        assert result.exit_code == 0
        assert "done." in result.output
        assert "test_app" in result.output

    def test_successful_install_then_restart(self, runner, product_workspace):
        """A feature installs (returncode 0) and the app starts → exit 0."""
        to_install = [("splent_feature_auth", "/workspace/splent_feature_auth", True)]

        def fake_run(cmd, *a, **k):
            return MagicMock(returncode=0, stdout="", stderr="")

        patchers = _patch_boundaries(to_install)
        for p in patchers:
            p.start()
        try:
            with (
                patch(
                    "splent_cli.commands.product.product_restart.run",
                    side_effect=fake_run,
                ),
                patch(
                    "splent_cli.commands.product.product_restart.subprocess.run",
                    return_value=MagicMock(returncode=0, stdout="", stderr=""),
                ),
            ):
                result = runner.invoke(product_restart, ["--dev"])
        finally:
            for p in patchers:
                p.stop()

        assert result.exit_code == 0
        assert "done." in result.output
        assert "install" in result.output

    def test_full_flag_skips_feature_detection(self, runner, product_workspace):
        """--full bypasses _detect_changes entirely; clean start → exit 0."""

        def fake_run(cmd, *a, **k):
            return MagicMock(returncode=0, stdout="", stderr="")

        # _detect_changes returns features, but --full must not invoke install.
        detect = MagicMock(return_value=[("splent_feature_auth", "/w/x", True)])
        with (
            patch(
                "splent_cli.commands.product.product_restart.compose.find_main_container",
                return_value="fake_container_id",
            ),
            patch(
                "splent_cli.commands.product.product_resolve.product_sync",
                _noop_sync,
            ),
            patch(
                "splent_cli.commands.product.product_restart._detect_changes",
                detect,
            ),
            patch(
                "splent_cli.commands.product.product_restart.run",
                side_effect=fake_run,
            ),
            patch(
                "splent_cli.commands.product.product_restart.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="", stderr=""),
            ),
        ):
            result = runner.invoke(product_restart, ["--full", "--dev"])

        assert result.exit_code == 0
        assert "done." in result.output
        assert detect.call_count == 0, "--full must skip feature change detection"


# ---------------------------------------------------------------------------
# Guard: no running container
# ---------------------------------------------------------------------------


class TestNoContainer:
    def test_no_running_container_exits_1(self, runner, product_workspace):
        with patch(
            "splent_cli.commands.product.product_restart.compose.find_main_container",
            return_value=None,
        ):
            result = runner.invoke(product_restart, ["--dev"])
        assert result.exit_code == 1
        assert "No running container" in result.output
        assert "done." not in result.output
