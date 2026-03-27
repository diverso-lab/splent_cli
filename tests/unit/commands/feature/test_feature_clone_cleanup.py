"""Tests for feature:clone — partial clone dir is cleaned up on failure."""
import subprocess
from unittest.mock import patch
from click.testing import CliRunner
from splent_cli.commands.feature.feature_clone import feature_clone

_RUN = "splent_cli.commands.feature.feature_clone.subprocess.run"


def _always_fail(cmd, **kwargs):
    raise subprocess.CalledProcessError(128, cmd)


class TestFeatureCloneCleanup:
    def test_no_partial_dir_after_both_failures(self, workspace):
        """If both clone attempts fail, no partial directory remains."""
        runner = CliRunner(mix_stderr=False)
        with patch(_RUN, side_effect=_always_fail):
            result = runner.invoke(feature_clone, ["testns/myrepo@v1.0.0"])

        assert result.exit_code == 1
        cache = workspace / ".splent_cache" / "features" / "testns"
        if cache.exists():
            entries = list(cache.iterdir())
            assert entries == [], f"Partial cache entries remain: {entries}"

    def test_error_message_on_clone_failure(self, workspace):
        """User gets a clear error message when clone fails."""
        runner = CliRunner(mix_stderr=False)
        with patch(_RUN, side_effect=_always_fail):
            result = runner.invoke(feature_clone, ["testns/myrepo@v1.0.0"])

        assert result.exit_code == 1
        assert (
            "not found" in result.output.lower()
            or "not accessible" in result.output.lower()
        )
