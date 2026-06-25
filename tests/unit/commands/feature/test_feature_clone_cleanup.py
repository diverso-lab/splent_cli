"""Tests for feature:clone — partial clone dir is cleaned up on failure."""
import os
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.feature.feature_clone import feature_clone

# feature_clone shells out via the proc.run wrapper (imported into the module
# as `run`); patch that, not subprocess directly. require_tool is patched so the
# absence of git on the test host doesn't short-circuit before the clone.
_RUN = "splent_cli.commands.feature.feature_clone.run"
_REQUIRE_TOOL = "splent_cli.commands.feature.feature_clone.require_tool"
_SSH = "splent_cli.utils.git_url._ssh_available"


def _always_fail(cmd, **kwargs):
    """Mimic git: create a partial dir at the destination, then report failure.

    The hardened code uses run(..., check=False) and inspects the returncode
    rather than relying on a raised exception, so we model failure that way.
    """
    dest = cmd[-1]
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "partial.txt"), "w") as fh:
        fh.write("partial clone artifact")
    return MagicMock(
        returncode=128,
        stderr="fatal: Remote branch v1.0.0 not found in upstream origin",
        stdout="",
    )


class TestFeatureCloneCleanup:
    def test_no_partial_dir_after_both_failures(self, workspace):
        """If both clone attempts fail, no partial directory remains."""
        runner = CliRunner(mix_stderr=False)
        with patch(_SSH, return_value=False), patch(_REQUIRE_TOOL), patch(
            _RUN, side_effect=_always_fail
        ):
            result = runner.invoke(feature_clone, ["testns/myrepo@v1.0.0"])

        assert result.exit_code == 1
        cache = workspace / ".splent_cache" / "features" / "testns"
        if cache.exists():
            entries = list(cache.iterdir())
            assert entries == [], f"Partial cache entries remain: {entries}"

    def test_error_message_on_clone_failure(self, workspace):
        """User gets a clear error message when clone fails."""
        runner = CliRunner(mix_stderr=False)
        with patch(_SSH, return_value=False), patch(_REQUIRE_TOOL), patch(
            _RUN, side_effect=_always_fail
        ):
            result = runner.invoke(feature_clone, ["testns/myrepo@v1.0.0"])

        assert result.exit_code == 1
        assert (
            "not found" in result.output.lower()
            or "not accessible" in result.output.lower()
        )
