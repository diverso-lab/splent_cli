"""Tests for uvl_sync metadata parsing — single and double quote support."""
import pytest
from splent_cli.commands.uvl.uvl_sync import _parse_feature_metadata_from_uvl_text


class TestParseFeatureMetadata:
    def test_parses_single_quoted_values(self):
        uvl_text = """
features
    myapp
        mandatory
            auth {org 'splent-io', package 'splent_feature_auth'}
"""
        meta = _parse_feature_metadata_from_uvl_text(uvl_text)
        assert "auth" in meta
        assert meta["auth"]["org"] == "splent-io"
        assert meta["auth"]["package"] == "splent_feature_auth"

    def test_parses_double_quoted_values(self):
        uvl_text = """
features
    myapp
        mandatory
            auth {org "splent-io", package "splent_feature_auth"}
"""
        meta = _parse_feature_metadata_from_uvl_text(uvl_text)
        assert "auth" in meta
        assert meta["auth"]["org"] == "splent-io"
        assert meta["auth"]["package"] == "splent_feature_auth"

    def test_parses_mixed_quotes(self):
        uvl_text = """
features
    myapp
        optional
            redis {org 'splent-io', package "splent_feature_redis"}
"""
        meta = _parse_feature_metadata_from_uvl_text(uvl_text)
        assert "redis" in meta
        assert meta["redis"]["package"] == "splent_feature_redis"

    def test_ignores_features_without_metadata(self):
        uvl_text = """
features
    myapp
        mandatory
            plain_feature
"""
        meta = _parse_feature_metadata_from_uvl_text(uvl_text)
        assert "plain_feature" not in meta

    def test_parses_multiple_features(self):
        uvl_text = """
features
    myapp
        mandatory
            auth {org 'splent-io', package 'splent_feature_auth'}
        optional
            redis {org 'splent-io', package 'splent_feature_redis'}
            mail {org "splent-io", package "splent_feature_mail"}
"""
        meta = _parse_feature_metadata_from_uvl_text(uvl_text)
        assert len(meta) == 3
        assert "auth" in meta
        assert "redis" in meta
        assert "mail" in meta

    def test_extra_spaces_around_braces(self):
        uvl_text = "    feat   {  org  'my-org'  ,  package  'my_pkg'  }  "
        meta = _parse_feature_metadata_from_uvl_text(uvl_text)
        assert "feat" in meta
        assert meta["feat"]["org"] == "my-org"
        assert meta["feat"]["package"] == "my_pkg"
