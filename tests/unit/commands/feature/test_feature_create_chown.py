"""Tests for feature:create — chown warning when permissions fail."""
from unittest.mock import patch
from click.testing import CliRunner
from splent_cli.commands.feature.feature_create import make_feature

_RENDER = "splent_cli.commands.feature.feature_create.render_and_write_file"
_CHOWN = "splent_cli.commands.feature.feature_create.os.chown"
_ISDIR = "splent_cli.commands.feature.feature_create.os.path.isdir"


def _mock_render(env, template_name, filename, ctx):
    """Stub that creates an empty file without needing real Jinja templates."""
    import os
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    open(filename, "w").close()


class TestChownWarning:
    def test_warns_when_chown_fails(self, workspace):
        runner = CliRunner(mix_stderr=False)
        with patch(_ISDIR, return_value=True):
            with patch(_RENDER, side_effect=_mock_render):
                with patch(_CHOWN, side_effect=PermissionError("no perm")):
                    result = runner.invoke(
                        make_feature, ["drorganvidez/test_feat"]
                    )

        assert result.exit_code == 0
        assert "✅" in result.output
        assert "⚠️" in result.output
        assert (
            "ownership" in result.output.lower()
            or "1000" in result.output
        )

    def test_succeeds_silently_when_chown_works(self, workspace):
        runner = CliRunner(mix_stderr=False)
        with patch(_ISDIR, return_value=True):
            with patch(_RENDER, side_effect=_mock_render):
                with patch(_CHOWN, return_value=None):
                    result = runner.invoke(
                        make_feature, ["drorganvidez/test_feat"]
                    )

        assert result.exit_code == 0
        assert "✅" in result.output
        assert "Could not set ownership" not in result.output
