"""Guard tests for feature:status — hardened error handling.

Focus:
  * A corrupt/malformed ``splent.manifest.json`` must not crash with a raw
    ``json.JSONDecodeError`` traceback; the command must surface a clean,
    file-naming error instead.
  * An ``ImportError`` from ``splent_framework`` (out-of-date / incompatible
    framework) must be turned into an actionable "framework out of date"
    hint rather than a bare traceback.

Plus a couple of core happy-path cases for the module.

These follow the established CliRunner(mix_stderr=False) + tmp workspace +
monkeypatch pattern (see test_feature_status.py / test_feature_clone.py).
"""

import json
import sys

import pytest
from click.testing import CliRunner
from unittest.mock import patch

from splent_cli.commands.feature.feature_status import feature_status
from splent_cli.utils.manifest import set_feature_state, feature_key


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _write_pyproject(product_path, features):
    import tomli_w

    data = {
        "project": {
            "name": "test_product",
            "optional-dependencies": {"features": features},
        }
    }
    with open(product_path / "pyproject.toml", "wb") as f:
        tomli_w.dump(data, f)


def _patch_context(workspace):
    """Patch context.require_app / context.workspace for the command."""
    return (
        patch(
            "splent_cli.commands.feature.feature_status.context.require_app",
            return_value="test_product",
        ),
        patch(
            "splent_cli.commands.feature.feature_status.context.workspace",
            return_value=workspace,
        ),
    )


def _seed_tracked_feature(product_path):
    """Create a manifest + matching pyproject entry so the status command
    reaches the rendering / framework-import path (not the early returns)."""
    _write_pyproject(product_path, ["splent_io/splent_feature_auth@v1.1.1"])
    key = feature_key("splent_io", "splent_feature_auth", "v1.1.1")
    set_feature_state(
        str(product_path),
        "test_product",
        key,
        "active",
        namespace="splent_io",
        name="splent_feature_auth",
        version="v1.1.1",
        mode="pinned",
    )


# ── Hardened: corrupt manifest ────────────────────────────────────────────


class TestCorruptManifest:
    def test_corrupt_manifest_clean_error_no_traceback(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        # Truncated / malformed JSON that json.load would choke on.
        (product_path / "splent.manifest.json").write_text('{"features": {')

        app_patch, ws_patch = _patch_context(workspace)
        with app_patch, ws_patch:
            result = runner.invoke(feature_status, [])

        # ClickException -> exit code 1, message on stderr, no raw traceback.
        assert result.exit_code != 0
        assert "Traceback" not in result.stderr
        assert "Traceback" not in result.output
        assert "JSONDecodeError" not in result.stderr
        # The error must name the offending file so the user can fix it.
        assert "splent.manifest.json" in result.stderr
        assert "JSON" in result.stderr

    def test_corrupt_manifest_json_flag_clean_error(self, runner, workspace):
        """The --json path also reads the manifest and must guard corruption."""
        product_path = workspace / "test_product"
        product_path.mkdir()
        (product_path / "splent.manifest.json").write_text("not json at all }}}")

        app_patch, ws_patch = _patch_context(workspace)
        with app_patch, ws_patch:
            result = runner.invoke(feature_status, ["--json"])

        assert result.exit_code != 0
        assert "Traceback" not in result.stderr
        assert "Traceback" not in result.output
        assert "splent.manifest.json" in result.stderr

    def test_corrupt_manifest_timeline_clean_error(self, runner, workspace):
        """The --timeline path also reads the manifest via _read_manifest_safe."""
        product_path = workspace / "test_product"
        product_path.mkdir()
        _write_pyproject(product_path, ["splent_io/splent_feature_auth@v1.1.1"])
        (product_path / "splent.manifest.json").write_text("{bad json")

        app_patch, ws_patch = _patch_context(workspace)
        with app_patch, ws_patch:
            result = runner.invoke(feature_status, ["--timeline"])

        assert result.exit_code != 0
        assert "Traceback" not in result.stderr
        assert "Traceback" not in result.output
        assert "splent.manifest.json" in result.stderr


# ── Hardened: framework ImportError ───────────────────────────────────────


class TestFrameworkImportError:
    def test_framework_import_error_gives_hint(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        _seed_tracked_feature(product_path)

        app_patch, ws_patch = _patch_context(workspace)
        # Setting the module to None in sys.modules makes the in-function
        # `from splent_framework.managers.migration_manager import ...`
        # raise ImportError, simulating an out-of-date framework.
        with (
            app_patch,
            ws_patch,
            patch.dict(
                sys.modules,
                {"splent_framework.managers.migration_manager": None},
            ),
        ):
            result = runner.invoke(feature_status, [])

        assert result.exit_code != 0
        # Clean ClickException, not a raw traceback.
        assert "Traceback" not in result.stderr
        assert "Traceback" not in result.output
        # Actionable "framework out of date / reinstall" style hint.
        lower = result.stderr.lower()
        assert "splent_framework" in lower
        assert "out of date" in lower or "incompatible" in lower
        assert "reinstall" in lower or "upgrade" in lower or "pip install" in lower


# ── Core happy paths ──────────────────────────────────────────────────────


class TestHappyPath:
    def test_renders_tracked_feature_state(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        _seed_tracked_feature(product_path)

        app_patch, ws_patch = _patch_context(workspace)
        with app_patch, ws_patch:
            result = runner.invoke(feature_status, [])

        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "active" in result.output
        assert "Traceback" not in result.output

    def test_json_flag_outputs_valid_json(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        _seed_tracked_feature(product_path)

        app_patch, ws_patch = _patch_context(workspace)
        with app_patch, ws_patch:
            result = runner.invoke(feature_status, ["--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "features" in data
        assert isinstance(data["features"], dict)

    def test_no_manifest_falls_back_to_pyproject(self, runner, workspace):
        product_path = workspace / "test_product"
        product_path.mkdir()
        _write_pyproject(product_path, ["splent_feature_auth@v1.1.1"])

        app_patch, ws_patch = _patch_context(workspace)
        with app_patch, ws_patch:
            result = runner.invoke(feature_status, [])

        assert result.exit_code == 0
        assert "splent.manifest.json" in result.output
        assert "splent_feature_auth" in result.output
