"""
Tests for feature:install.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
import pytest


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def feature_install(monkeypatch):
    # Needed because compose imports splent_framework in this test env.
    sys.modules.pop("splent_cli.commands.feature.feature_install", None)
    sys.modules.pop("splent_cli.services.compose", None)

    monkeypatch.setitem(
        sys.modules,
        "splent_cli.utils.feature_utils",
        types.SimpleNamespace(
            normalize_namespace=lambda value: value.replace("-", "_"),
            read_features_from_data=lambda data, env: data.get("project", {})
            .get("optional-dependencies", {})
            .get("features", []),
        ),
    )

    return importlib.import_module(
        "splent_cli.commands.feature.feature_install"
    )


def _feature_pyproject(path, requires=None):
    requires = requires or []
    path.write_text(
        "[project]\n"
        'name = "splent_feature_auth"\n'
        "\n"
        "[tool.splent.contract.requires]\n"
        f"features = {requires!r}\n"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_reads_required_features(self, tmp_path, feature_install):
        pyproject = tmp_path / "pyproject.toml"
        _feature_pyproject(pyproject, ["auth", "billing"])

        result = feature_install._get_required_features(str(pyproject))

        assert result == ["auth", "billing"]

    def test_versions_are_sorted(self, feature_install):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"name": "v1.0.0"},
            {"name": "v2.0.0"},
            {"name": "abc"},
        ]

        with patch(
            "splent_cli.commands.feature.feature_install.requests.get",
            return_value=response,
        ):
            versions = feature_install._get_available_versions(
                "splent-io", "splent_feature_auth"
            )

        assert versions == ["v2.0.0", "v1.0.0"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestFeatureInstallCommand:
    def test_pinned_cached_feature_attaches_version(
        self, runner, product_workspace, feature_install
    ):
        cache_dir = (
            product_workspace
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_auth@v1.0.0"
        )
        cache_dir.mkdir(parents=True)
        _feature_pyproject(cache_dir / "pyproject.toml")

        with (
            patch(
                "splent_cli.commands.feature.feature_install.marketplace.require_marketplace_login",
                return_value=None,
            ),
            patch(
                "splent_cli.commands.feature.feature_install.get_package_by_name",
                return_value={
                    "name": "splent_feature_auth",
                    "full_name": "splent-io/splent_feature_auth@v1.0.0",
                },
            ),
            patch(
                "splent_cli.commands.feature.feature_install.compose.resolve_file",
                return_value=None,
            ),
            patch(
                "splent_cli.commands.feature.feature_install.subprocess.run",
                return_value=MagicMock(returncode=0, stderr=""),
            ) as run,
        ):
            result = runner.invoke(
                feature_install.feature_install,
                ["splent-io/auth", "--pinned", "--version", "v1.0.0"],
            )

        calls = [call.args[0] for call in run.call_args_list]

        assert result.exit_code == 0
        assert [
            "splent",
            "feature:attach",
            "splent-io/splent_feature_auth",
            "v1.0.0",
        ] in calls
        assert "auth installed" in result.output
