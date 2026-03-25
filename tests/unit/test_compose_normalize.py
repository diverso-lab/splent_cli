"""
Tests for compose.normalize_feature_ref — org normalization.
"""
from splent_cli.services.compose import normalize_feature_ref


class TestNormalizeFeatureRef:

    def test_dash_org_normalized_to_underscore(self):
        assert normalize_feature_ref("splent-io/splent_feature_auth@v1.2.1") == \
            "splent_io/splent_feature_auth@v1.2.1"

    def test_dot_org_normalized_to_underscore(self):
        assert normalize_feature_ref("splent.io/splent_feature_auth") == \
            "splent_io/splent_feature_auth"

    def test_already_normalized_unchanged(self):
        assert normalize_feature_ref("splent_io/splent_feature_auth") == \
            "splent_io/splent_feature_auth"

    def test_no_org_defaults_to_splent_io(self):
        assert normalize_feature_ref("splent_feature_auth") == \
            "splent_io/splent_feature_auth"

    def test_strips_features_prefix(self):
        assert normalize_feature_ref("features/splent_io/splent_feature_auth") == \
            "splent_io/splent_feature_auth"

    def test_versioned_with_dash_org(self):
        assert normalize_feature_ref("splent-io/splent_feature_redis@v1.2.0") == \
            "splent_io/splent_feature_redis@v1.2.0"
