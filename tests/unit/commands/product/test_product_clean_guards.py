"""
Regression net for the *hardened* safety behaviors of ``product:clean``.

The command performs a NUCLEAR reset of a product environment. The hardening
under test here is specifically around the destructive ``docker compose down -v``
step (which deletes all volumes / data):

  * it must require explicit confirmation and warn that volumes / data are
    deleted (irreversible);
  * a failure of the compose command must be SURFACED, not silently reported
    as "stopped";
  * stderr must NOT be swallowed on the destructive op (no capture_output).

All subprocess access is mocked at the boundary. ``product_clean`` shells out via
``splent_cli.utils.proc.run`` (which calls ``subprocess.run``) and gates the
destructive step behind ``require_docker`` (which also calls
``splent_cli.utils.proc.subprocess.run`` for ``docker info``). Patching
``splent_cli.utils.proc.subprocess.run`` therefore covers BOTH, and
``splent_cli.utils.proc.shutil.which`` makes the docker-on-PATH check pass.

No real docker / git / network / DB is touched.
"""

import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_clean import product_clean


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _ok(cmd, **kwargs):
    """Every subprocess call succeeds."""
    return MagicMock(returncode=0, stdout="", stderr="")


def _down_fails(cmd, **kwargs):
    """`docker compose ... down -v` fails; everything else succeeds.

    `docker info` (the require_docker probe) must still succeed so the command
    reaches the destructive step.
    """
    if "down" in cmd:
        return MagicMock(returncode=1, stdout="", stderr="compose error")
    return MagicMock(returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Hardened behavior 1: destructive op requires confirmation + warns about data
# ---------------------------------------------------------------------------


class TestDestructiveConfirmation:
    def test_warns_volumes_and_data_deleted(self, runner, product_workspace):
        """The confirmation screen must spell out that volumes/data are wiped."""
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok),
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            result = runner.invoke(product_clean, ["--dev"], input="n\n")
        combined = result.output.lower()
        assert "volume" in combined
        assert "irreversible" in combined or "delete" in combined

    def test_no_yes_and_decline_aborts_before_docker(self, runner, product_workspace):
        """Declining the prompt must stop BEFORE any docker call runs."""
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok) as mock_run,
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            result = runner.invoke(product_clean, ["--dev"], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        # Nothing destructive should have been executed.
        assert mock_run.call_count == 0

    def test_prompt_required_without_yes(self, runner, product_workspace):
        """With no --yes and no input, click should not silently proceed."""
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok),
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            # Empty stdin → click.confirm gets EOF → aborts (non-success / no clean).
            result = runner.invoke(product_clean, ["--dev"])
        assert "fully cleaned" not in result.output


# ---------------------------------------------------------------------------
# Hardened behavior 2: a failed `down -v` is SURFACED, not reported as stopped
# ---------------------------------------------------------------------------


class TestDownFailureSurfaced:
    def test_failure_does_not_claim_stopped(self, runner, product_workspace):
        """A non-zero `down -v` must NOT print the success 'stopped' message."""
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_down_fails),
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert "stopped and volumes removed" not in result.output

    def test_failure_emits_warning_with_exit_code(self, runner, product_workspace):
        """A failed destructive op must surface a warning mentioning the failure."""
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_down_fails),
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        out = result.output.lower()
        assert "failed" in out
        assert "test_app" in result.output  # names the offending project

    def test_no_traceback_on_down_failure(self, runner, product_workspace):
        """Failure is handled cleanly: no raw traceback leaks to the user."""
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_down_fails),
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert "Traceback" not in result.output
        assert "Traceback" not in (result.stderr or "")
        assert "CalledProcessError" not in result.output


# ---------------------------------------------------------------------------
# Hardened behavior 3: stderr is NOT swallowed on the destructive op
# ---------------------------------------------------------------------------


class TestStderrNotSwallowed:
    def test_down_call_does_not_capture_output(self, runner, product_workspace):
        """`down -v` must let stderr stream through (no capture_output=True).

        If output were captured, compose errors would be hidden from the user.
        We assert the actual subprocess invocation for the destructive command
        did not request output capture.
        """
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok) as mock_run,
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            runner.invoke(product_clean, ["--dev", "--yes"])

        down_calls = [
            c
            for c in mock_run.call_args_list
            if isinstance(c.args[0], (list, tuple)) and "down" in c.args[0]
        ]
        assert down_calls, "expected a `docker compose ... down -v` invocation"
        for call in down_calls:
            assert call.kwargs.get("capture_output") in (False, None)

    def test_down_invocation_uses_down_v(self, runner, product_workspace):
        """Sanity: the destructive command really is `down -v`."""
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok) as mock_run,
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            runner.invoke(product_clean, ["--dev", "--yes"])
        down_calls = [
            c.args[0]
            for c in mock_run.call_args_list
            if isinstance(c.args[0], (list, tuple)) and "down" in c.args[0]
        ]
        assert any(
            "down" in cmd and "-v" in cmd and "docker" in cmd for cmd in down_calls
        )


# ---------------------------------------------------------------------------
# Hardened behavior 4: docker availability is checked before wiping
# ---------------------------------------------------------------------------


class TestRequiresDocker:
    def test_aborts_cleanly_when_docker_missing(self, runner, product_workspace):
        """If docker is not on PATH, fail with a clean message (no traceback)."""
        with (
            patch("splent_cli.utils.proc.shutil.which", return_value=None),
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok) as mock_run,
        ):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert result.exit_code != 0
        assert "Traceback" not in (result.stderr or "")
        assert "Traceback" not in result.output
        assert "docker" in (result.stderr + result.output).lower()
        # Must not have proceeded to wipe anything.
        assert mock_run.call_count == 0

    def test_aborts_when_daemon_unreachable(self, runner, product_workspace):
        """docker on PATH but `docker info` fails → clean abort, no wipe."""

        def which_ok_info_fail(cmd, **kwargs):
            if "info" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="Cannot connect to the Docker daemon",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        with (
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "splent_cli.utils.proc.subprocess.run", side_effect=which_ok_info_fail
            ) as mock_run,
        ):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert result.exit_code != 0
        assert "Traceback" not in (result.stderr or "")
        # No `down -v` should have run.
        down_calls = [
            c
            for c in mock_run.call_args_list
            if isinstance(c.args[0], (list, tuple)) and "down" in c.args[0]
        ]
        assert not down_calls


# ---------------------------------------------------------------------------
# Core happy-path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_full_clean_succeeds(self, runner, product_workspace):
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok),
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            result = runner.invoke(product_clean, ["--dev", "--yes"])
        assert result.exit_code == 0
        assert "stopped and volumes removed" in result.output
        assert "fully cleaned" in result.output

    def test_both_env_flags_rejected_before_any_subprocess(
        self, runner, product_workspace
    ):
        with (
            patch("splent_cli.utils.proc.subprocess.run", side_effect=_ok) as mock_run,
            patch("splent_cli.utils.proc.shutil.which", return_value="/usr/bin/docker"),
        ):
            result = runner.invoke(product_clean, ["--dev", "--prod", "--yes"])
        assert result.exit_code == 1
        assert "Cannot specify both" in result.output
        assert mock_run.call_count == 0
