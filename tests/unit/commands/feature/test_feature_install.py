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
    def test_feature_short_name_normalizes_supported_refs(self, feature_install):
        assert feature_install._feature_short_name("auth") == "auth"
        assert feature_install._feature_short_name("splent_feature_auth") == "auth"
        assert (
            feature_install._feature_short_name("splent-io/splent_feature_auth@v1")
            == "auth"
        )

    def test_reads_required_features_from_marketplace_contract(self, feature_install):
        package = {
            "contract": {
                "requires": {
                    "features": [
                        "auth",
                        "splent_feature_profile",
                        "splent-io/splent_feature_mail@v1",
                    ]
                }
            }
        }

        assert feature_install._get_marketplace_required_features(package) == [
            "auth",
            "profile",
            "mail",
        ]

    def test_feature_api_candidates_try_namespaced_value_first(self, feature_install):
        assert feature_install._feature_api_candidates("splent-io/profile") == [
            "splent-io/profile",
            "splent-io/splent_feature_profile",
            "splent_feature_profile",
        ]

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
    def test_missing_marketplace_dependency_aborts_before_install_steps(
        self, runner, product_workspace, feature_install
    ):
        with (
            patch(
                "splent_cli.commands.feature.feature_install.marketplace.require_marketplace_login",
                return_value=None,
            ),
            patch(
                "splent_cli.commands.feature.feature_install.get_package_by_name",
                return_value={
                    "name": "splent_feature_profile",
                    "full_name": "splent-io/splent_feature_profile@v1.0.0",
                    "contract": {"requires": {"features": ["auth"]}},
                },
            ),
            patch(
                "splent_cli.commands.feature.feature_install.subprocess.run",
                return_value=MagicMock(returncode=0, stderr=""),
            ) as run,
        ):
            result = runner.invoke(
                feature_install.feature_install,
                ["splent-io/profile", "--pinned", "--version", "v1.0.0"],
            )

        assert result.exit_code == 1
        assert "Cannot install profile" in result.output
        assert "auth" in result.output
        assert "splent feature:install splent-io/splent_feature_auth" in result.output
        run.assert_not_called()

    def test_namespaced_install_falls_back_to_normalized_api_name(
        self, runner, product_workspace, feature_install
    ):
        cache_dir = (
            product_workspace
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_profile@v1.0.0"
        )
        cache_dir.mkdir(parents=True)
        _feature_pyproject(cache_dir / "pyproject.toml")

        def fake_get_package(name):
            if name == "splent-io/profile":
                raise feature_install.SplentAPIError("SPLENT API returned HTTP 404.")
            return {
                "name": "splent_feature_profile",
                "full_name": "splent-io/splent_feature_profile@v1.0.0",
                "contract": {"requires": {"features": []}},
            }

        with (
            patch(
                "splent_cli.commands.feature.feature_install.marketplace.require_marketplace_login",
                return_value=None,
            ),
            patch(
                "splent_cli.commands.feature.feature_install.get_package_by_name",
                side_effect=fake_get_package,
            ) as get_package,
            patch(
                "splent_cli.commands.feature.feature_install.compose.resolve_file",
                return_value=None,
            ),
            patch(
                "splent_cli.commands.feature.feature_install.subprocess.run",
                return_value=MagicMock(returncode=0, stderr=""),
            ),
        ):
            result = runner.invoke(
                feature_install.feature_install,
                ["splent-io/profile", "--pinned", "--version", "v1.0.0"],
            )

        assert result.exit_code == 0
        assert [call.args[0] for call in get_package.call_args_list] == [
            "splent-io/profile",
            "splent-io/splent_feature_profile",
        ]

    def test_missing_marketplace_package_reports_not_published(
        self, runner, product_workspace, feature_install
    ):
        with (
            patch(
                "splent_cli.commands.feature.feature_install.marketplace.require_marketplace_login",
                return_value=None,
            ),
            patch(
                "splent_cli.commands.feature.feature_install.get_package_by_name",
                side_effect=feature_install.SplentAPIError(
                    "SPLENT API returned HTTP 500."
                ),
            ),
            patch(
                "splent_cli.commands.feature.feature_install.get_packages",
                return_value=[],
            ),
        ):
            result = runner.invoke(
                feature_install.feature_install,
                ["splent-io/splent_feature_auth"],
            )

        assert result.exit_code == 1
        assert "not published in the Marketplace" in result.output

    def test_marketplace_dependency_already_declared_allows_install(
        self, runner, product_workspace, feature_install
    ):
        pyproject = product_workspace / "test_app" / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\n'
            'features = ["splent-io/splent_feature_auth"]\n'
        )

        cache_dir = (
            product_workspace
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_profile@v1.0.0"
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
                    "name": "splent_feature_profile",
                    "full_name": "splent-io/splent_feature_profile@v1.0.0",
                    "contract": {"requires": {"features": ["auth"]}},
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
                ["splent-io/profile", "--pinned", "--version", "v1.0.0"],
            )

        calls = [call.args[0] for call in run.call_args_list]

        assert result.exit_code == 0
        assert [
            "splent",
            "feature:attach",
            "splent-io/splent_feature_profile",
            "v1.0.0",
        ] in calls

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
