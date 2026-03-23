"""
Tests for splent_cli.utils.feature_utils

This module is a shim — it re-exports from splent_framework and adds one
CLI-specific helper: get_normalize_feature_name_in_splent_format().
"""
from splent_cli.utils.feature_utils import get_normalize_feature_name_in_splent_format


class TestNormalizeFeatureName:
    def test_adds_prefix_when_missing(self):
        assert get_normalize_feature_name_in_splent_format("auth") == "splent_feature_auth"

    def test_no_double_prefix(self):
        assert get_normalize_feature_name_in_splent_format("splent_feature_auth") == "splent_feature_auth"

    def test_preserves_full_name(self):
        assert get_normalize_feature_name_in_splent_format("splent_feature_profile") == "splent_feature_profile"

    def test_bare_name_with_underscores(self):
        assert get_normalize_feature_name_in_splent_format("my_feature") == "splent_feature_my_feature"
