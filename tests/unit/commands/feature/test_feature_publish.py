"""
Tests for feature:publish.
"""

import importlib
import sys
import types
from unittest.mock import patch

import pytest


@pytest.fixture
def feature_publish_module(monkeypatch):
    # Avoid importing heavy feature_contract dependencies in these unit tests.
    module_name = "splent_cli.commands.feature.feature_publish"
    previous_module = sys.modules.pop(module_name, None)

    monkeypatch.setitem(
        sys.modules,
        "tomli_w",
        types.SimpleNamespace(dumps=lambda data: ""),
    )
    monkeypatch.setitem(
        sys.modules,
        "splent_cli.commands.feature.feature_contract",
        types.SimpleNamespace(
            _resolve_feature=lambda full_name, workspace: None,
            infer_contract=lambda feature_path, namespace, name: {},
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "splent_cli.utils.feature_utils",
        types.SimpleNamespace(normalize_namespace=lambda namespace: namespace),
    )

    module = importlib.import_module(module_name)
    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)
        if previous_module is not None:
            sys.modules[module_name] = previous_module


# ---------------------------------------------------------------------------
# Name parsing
# ---------------------------------------------------------------------------


class TestParseFullName:
    def test_default_owner_is_splent_io(self, feature_publish_module):
        assert feature_publish_module._parse_full_name(
            "splent_feature_auth"
        ) == (
            "splent-io",
            "splent_feature_auth",
            None,
        )

    def test_owner_underscores_are_normalized_to_dashes(
        self, feature_publish_module
    ):
        assert feature_publish_module._parse_full_name(
            "splent_io/splent_feature_auth@v1"
        ) == (
            "splent-io",
            "splent_feature_auth",
            "v1",
        )


# ---------------------------------------------------------------------------
# Git remotes
# ---------------------------------------------------------------------------


class TestRepoFromRemote:
    def test_extracts_github_ssh_remote_without_git_suffix(
        self, feature_publish_module
    ):
        assert feature_publish_module._repo_from_remote(
            "git@github.com:splent-io/splent_feature_auth.git"
        ) == ("splent-io", "splent_feature_auth")

    def test_extracts_github_https_remote_with_token(
        self, feature_publish_module
    ):
        assert feature_publish_module._repo_from_remote(
            "https://ghp_secret@github.com/splent-io/auth.git"
        ) == ("splent-io", "auth")


class TestBrowserUrl:
    def test_converts_github_ssh_remote_to_browser_url(
        self, feature_publish_module
    ):
        assert (
            feature_publish_module._browser_repo_url(
                "git@github.com:splent-io/auth.git"
            )
            == "https://github.com/splent-io/auth"
        )

    def test_removes_token_and_git_suffix_from_github_https_remote(
        self, feature_publish_module
    ):
        assert (
            feature_publish_module._browser_repo_url(
                "https://token@github.com/splent-io/auth.git"
            )
            == "https://github.com/splent-io/auth"
        )

    def test_removes_query_and_git_suffix_from_generic_https_remote(
        self, feature_publish_module
    ):
        assert (
            feature_publish_module._browser_repo_url(
                "https://git.example.com/org/auth.git?token=secret"
            )
            == "https://git.example.com/org/auth"
        )


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


class TestBuildPayload:
    def test_payload_uses_browser_repo_url_without_token_or_git_suffix(
        self, tmp_path, feature_publish_module
    ):
        feature_path = tmp_path / "splent_feature_auth"
        feature_path.mkdir()

        with (
            patch(
                "splent_cli.commands.feature.feature_publish.infer_contract",
                return_value={},
            ),
            patch(
                "splent_cli.commands.feature.feature_publish._git_remote_url",
                return_value="https://token@github.com/splent-io/auth.git",
            ),
        ):
            payload = feature_publish_module._build_payload(
                feature_path,
                "splent_io",
                "splent_feature_auth",
                "splent_io/splent_feature_auth@v1",
            )

        assert payload["full_name"] == "splent-io/splent_feature_auth@v1"
        assert payload["repo_url"] == "https://github.com/splent-io/auth"
        assert payload["github"]["url"] == "https://github.com/splent-io/auth"
        assert "token" not in payload["repo_url"]

    def test_payload_falls_back_to_browser_github_url(
        self, tmp_path, feature_publish_module
    ):
        feature_path = tmp_path / "splent_feature_auth"
        feature_path.mkdir()

        with (
            patch(
                "splent_cli.commands.feature.feature_publish.infer_contract",
                return_value={},
            ),
            patch(
                "splent_cli.commands.feature.feature_publish._git_remote_url",
                return_value=None,
            ),
        ):
            payload = feature_publish_module._build_payload(
                feature_path,
                "splent_io",
                "splent_feature_auth",
                "splent_io/splent_feature_auth",
            )

        assert (
            payload["repo_url"]
            == "https://github.com/splent-io/splent_feature_auth"
        )

    def test_canonical_full_name_preserves_version_when_present(
        self, feature_publish_module
    ):
        assert (
            feature_publish_module._canonical_full_name(
                "splent-io", "splent_feature_auth", "v1"
            )
            == "splent-io/splent_feature_auth@v1"
        )
