"""
Tests for feature:translate — hardened guards.

Focus (hardened behaviors first):
  * Babel package missing            -> clean ClickException, no traceback.
  * pybabel/python tool missing      -> clean ClickException (FileNotFoundError
    at the subprocess boundary is translated by proc.run, not a raw traceback).
  * The temporary babel.cfg generated for extraction is ALWAYS cleaned up and
    never left behind in the feature directory, even when extraction fails.

Then a couple of core happy-path cases.

Boundary mocking (no real babel / git / network / subprocess):
  * _run_pybabel shells out via splent_cli.utils.proc.run -> patch
    "splent_cli.utils.proc.subprocess.run" to return a fake CompletedProcess
    or raise FileNotFoundError.
  * Babel availability is probed via importlib.util.find_spec -> patch
    "splent_cli.commands.feature.feature_translate.importlib.util.find_spec".
"""

import os
import subprocess
from unittest.mock import patch, MagicMock

import click
import pytest
from click.testing import CliRunner

from splent_cli.commands.feature.feature_translate import (
    feature_translate,
    _require_babel,
    _extract_feature,
    _run_pybabel,
)


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _fake_completed(returncode=0, stdout="", stderr=""):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _make_feature(workspace, ns_safe="splent_io", name="splent_feature_auth"):
    """Create an editable feature at the workspace root and declare it in the
    product's pyproject.toml. Returns the feature root directory."""
    root = workspace / name
    src = root / "src" / ns_safe / name
    (src / "templates").mkdir(parents=True)
    (src / "__init__.py").write_text("msg = 'hello'\n")

    pyproject = workspace / "test_app" / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n'
        "[tool.splent]\n"
        f'features = ["{ns_safe.replace("_", "-")}/{name}"]\n'
    )
    return root


# ---------------------------------------------------------------------------
# Hardened: Babel package missing -> clean ClickException
# ---------------------------------------------------------------------------


class TestBabelMissing:
    def test_require_babel_raises_clickexception(self):
        with patch(
            "splent_cli.commands.feature.feature_translate.importlib.util.find_spec",
            return_value=None,
        ):
            with pytest.raises(click.ClickException) as exc:
                _require_babel()
        assert "Babel" in str(exc.value.message)
        assert "pip install" in str(exc.value.message)

    def test_command_clean_error_when_babel_missing(self, runner, product_workspace):
        # Babel reported as unavailable -> command must abort with a clean
        # message (exit 1) and NOT a raw traceback.
        with patch(
            "splent_cli.commands.feature.feature_translate.importlib.util.find_spec",
            return_value=None,
        ):
            result = runner.invoke(feature_translate, ["--extract"])
        assert result.exit_code == 1
        assert "Babel" in result.stderr
        assert "Traceback" not in result.stderr
        assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# Hardened: pybabel tool missing at the subprocess boundary -> clean error
# ---------------------------------------------------------------------------


class TestToolMissingAtBoundary:
    def test_run_pybabel_filenotfound_becomes_clickexception(self, tmp_path):
        # Simulate the interpreter/pybabel entrypoint being unavailable: the
        # proc.run wrapper must translate FileNotFoundError into a clean
        # ClickException naming the tool (no raw OSError / traceback).
        with patch(
            "splent_cli.utils.proc.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            with pytest.raises(click.ClickException) as exc:
                _run_pybabel(["extract", "src/"], cwd=str(tmp_path))
        msg = str(exc.value.message)
        assert "not installed" in msg or "not on PATH" in msg
        assert "Traceback" not in msg


# ---------------------------------------------------------------------------
# Hardened: temporary babel.cfg is never left behind
# ---------------------------------------------------------------------------


class TestBabelCfgCleanup:
    def _redirect_mkstemp(self, tmp_path):
        """Force tempfile.mkstemp to create the babel cfg inside tmp_path so we
        can verify it is removed afterwards. Returns the path holder."""
        real_open = os.open
        holder = {}

        def fake_mkstemp(prefix="", suffix="", dir=None):
            p = str(tmp_path / f"{prefix}cfg{suffix}")
            holder["path"] = p
            fd = real_open(p, os.O_RDWR | os.O_CREAT, 0o600)
            return fd, p

        return fake_mkstemp, holder

    def test_cfg_removed_after_failed_extract(self, tmp_path):
        feature_root = tmp_path / "feat"
        src_dir = feature_root / "src"
        translations_dir = src_dir / "translations"
        src_dir.mkdir(parents=True)

        fake_mkstemp, holder = self._redirect_mkstemp(tmp_path)

        with (
            patch(
                "splent_cli.commands.feature.feature_translate.tempfile.mkstemp",
                side_effect=fake_mkstemp,
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                return_value=_fake_completed(returncode=1, stderr="boom"),
            ),
        ):
            ok = _extract_feature(
                str(feature_root), str(src_dir), str(translations_dir), "auth"
            )

        assert ok is False
        # The generated temp cfg must be gone.
        assert "path" in holder
        assert not os.path.exists(holder["path"])
        # And nothing called babel.cfg should be left in the feature tree.
        leftovers = []
        for root, _dirs, files in os.walk(feature_root):
            leftovers += [f for f in files if f.endswith(".cfg")]
        assert leftovers == []

    def test_cfg_removed_after_successful_extract(self, tmp_path):
        feature_root = tmp_path / "feat"
        src_dir = feature_root / "src"
        translations_dir = src_dir / "translations"
        src_dir.mkdir(parents=True)

        fake_mkstemp, holder = self._redirect_mkstemp(tmp_path)

        with (
            patch(
                "splent_cli.commands.feature.feature_translate.tempfile.mkstemp",
                side_effect=fake_mkstemp,
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                return_value=_fake_completed(returncode=0, stdout="ok"),
            ),
        ):
            ok = _extract_feature(
                str(feature_root), str(src_dir), str(translations_dir), "auth"
            )

        assert ok is True
        assert not os.path.exists(holder["path"])
        leftovers = []
        for root, _dirs, files in os.walk(feature_root):
            leftovers += [f for f in files if f.endswith(".cfg")]
        assert leftovers == []

    def test_cfg_removed_even_when_pybabel_tool_missing(self, tmp_path):
        # FileNotFoundError from the subprocess boundary propagates as a
        # ClickException, but the finally block must still remove the cfg.
        feature_root = tmp_path / "feat"
        src_dir = feature_root / "src"
        translations_dir = src_dir / "translations"
        src_dir.mkdir(parents=True)

        fake_mkstemp, holder = self._redirect_mkstemp(tmp_path)

        with (
            patch(
                "splent_cli.commands.feature.feature_translate.tempfile.mkstemp",
                side_effect=fake_mkstemp,
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                side_effect=FileNotFoundError(),
            ),
        ):
            with pytest.raises(click.ClickException):
                _extract_feature(
                    str(feature_root), str(src_dir), str(translations_dir), "auth"
                )

        assert "path" in holder
        assert not os.path.exists(holder["path"])


# ---------------------------------------------------------------------------
# Core happy-path / behavior
# ---------------------------------------------------------------------------


class TestNoFlags:
    def test_no_action_flag_prompts_and_does_nothing(self, runner, product_workspace):
        # Without --extract/--init/--compile the command should not shell out
        # and should not even probe babel.
        with (
            patch("splent_cli.utils.proc.subprocess.run") as mock_run,
            patch(
                "splent_cli.commands.feature.feature_translate.importlib.util.find_spec"
            ) as mock_find,
        ):
            result = runner.invoke(feature_translate, [])
        assert result.exit_code == 0
        assert "Specify" in result.output
        mock_run.assert_not_called()
        mock_find.assert_not_called()


class TestExtractHappyPath:
    def test_extract_invokes_pybabel_and_reports(self, runner, product_workspace):
        _make_feature(product_workspace)

        with (
            patch(
                "splent_cli.commands.feature.feature_translate.importlib.util.find_spec",
                return_value=object(),
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                return_value=_fake_completed(returncode=0, stdout="extracted"),
            ) as mock_run,
        ):
            result = runner.invoke(feature_translate, ["auth", "--extract"])

        assert result.exit_code == 0, result.stderr
        assert "Traceback" not in result.output
        assert "extracted to messages.pot" in result.output
        # pybabel was actually invoked with the extract subcommand.
        assert mock_run.called
        called_cmd = mock_run.call_args.args[0]
        assert "extract" in called_cmd

    def test_extract_creates_translations_dir(self, runner, product_workspace):
        root = _make_feature(product_workspace)
        translations = (
            root / "src" / "splent_io" / "splent_feature_auth" / "translations"
        )
        assert not translations.exists()

        with (
            patch(
                "splent_cli.commands.feature.feature_translate.importlib.util.find_spec",
                return_value=object(),
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                return_value=_fake_completed(returncode=0, stdout="ok"),
            ),
        ):
            result = runner.invoke(feature_translate, ["auth", "--extract"])

        assert result.exit_code == 0, result.stderr
        assert translations.is_dir()


class TestNoFeaturesDeclared:
    def test_reports_when_no_features(self, runner, product_workspace):
        # Default product_workspace pyproject declares no features.
        with (
            patch(
                "splent_cli.commands.feature.feature_translate.importlib.util.find_spec",
                return_value=object(),
            ),
            patch("splent_cli.utils.proc.subprocess.run") as mock_run,
        ):
            result = runner.invoke(feature_translate, ["--extract"])
        assert result.exit_code == 0
        assert "No features declared." in result.output
        mock_run.assert_not_called()
