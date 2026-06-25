"""Tests for pure helper functions in uvl_sync.py."""
import pytest

from splent_cli.commands.product.product_auto_require import (
    _build_req_graph,
    _closure_requires,
    _dep_spec_from_meta,
    _parse_feature_metadata_from_uvl_text,
)
from splent_cli.utils.feature_utils import (
    read_features_from_data,
    write_features_to_data,
)

import click


# ---------------------------------------------------------------------------
# _build_req_graph
# ---------------------------------------------------------------------------

class TestBuildReqGraph:
    def test_simple_pair(self):
        graph = _build_req_graph([("a", "b")])
        assert "b" in graph["a"]

    def test_multiple_deps_for_same_source(self):
        graph = _build_req_graph([("a", "b"), ("a", "c")])
        assert graph["a"] == {"b", "c"}

    def test_empty_pairs(self):
        graph = _build_req_graph([])
        assert dict(graph) == {}

    def test_chain(self):
        graph = _build_req_graph([("a", "b"), ("b", "c")])
        assert "b" in graph["a"]
        assert "c" in graph["b"]

    def test_duplicate_pairs_deduped(self):
        graph = _build_req_graph([("a", "b"), ("a", "b")])
        assert graph["a"] == {"b"}


# ---------------------------------------------------------------------------
# _closure_requires
# ---------------------------------------------------------------------------

class TestClosureRequires:
    def test_direct_dep(self):
        graph = _build_req_graph([("auth", "core")])
        result = _closure_requires({"auth"}, graph)
        assert "core" in result

    def test_transitive_dep(self):
        graph = _build_req_graph([("profile", "auth"), ("auth", "core")])
        result = _closure_requires({"profile"}, graph)
        assert "auth" in result
        assert "core" in result

    def test_no_deps(self):
        graph = _build_req_graph([])
        result = _closure_requires({"auth"}, graph)
        assert result == set()

    def test_selected_not_in_result(self):
        graph = _build_req_graph([("auth", "core")])
        result = _closure_requires({"auth"}, graph)
        assert "auth" not in result

    def test_cycle_does_not_hang(self):
        graph = _build_req_graph([("a", "b"), ("b", "a")])
        result = _closure_requires({"a"}, graph)
        assert "b" in result

    def test_diamond_dep(self):
        graph = _build_req_graph([("c", "a"), ("c", "b"), ("a", "base"), ("b", "base")])
        result = _closure_requires({"c"}, graph)
        assert "a" in result
        assert "b" in result
        assert "base" in result

    def test_empty_selected(self):
        graph = _build_req_graph([("a", "b")])
        result = _closure_requires(set(), graph)
        assert result == set()


# ---------------------------------------------------------------------------
# _parse_feature_metadata_from_uvl_text
# ---------------------------------------------------------------------------

class TestParseFeatureMetadataFromUvlText:
    def test_parses_org_and_package(self):
        text = "    auth {org 'splent-io' package 'splent_feature_auth'}\n"
        meta = _parse_feature_metadata_from_uvl_text(text)
        assert meta["auth"]["org"] == "splent-io"
        assert meta["auth"]["package"] == "splent_feature_auth"

    def test_double_quoted_values(self):
        text = '    profile {org "splent-io" package "splent_feature_profile"}\n'
        meta = _parse_feature_metadata_from_uvl_text(text)
        assert meta["profile"]["package"] == "splent_feature_profile"

    def test_multiple_features(self):
        text = (
            "    auth {org 'x' package 'splent_feature_auth'}\n"
            "    blog {org 'x' package 'splent_feature_blog'}\n"
        )
        meta = _parse_feature_metadata_from_uvl_text(text)
        assert "auth" in meta
        assert "blog" in meta

    def test_ignores_lines_without_braces(self):
        text = "features\n    mandatory\n    auth {org 'x' package 'p'}\n"
        meta = _parse_feature_metadata_from_uvl_text(text)
        assert "auth" in meta
        assert "mandatory" not in meta

    def test_empty_text(self):
        assert _parse_feature_metadata_from_uvl_text("") == {}

    def test_no_fields_in_braces_excluded(self):
        text = "    auth {}\n"
        meta = _parse_feature_metadata_from_uvl_text(text)
        assert "auth" not in meta


# ---------------------------------------------------------------------------
# _dep_spec_from_meta
# ---------------------------------------------------------------------------

class TestDepSpecFromMeta:
    def test_with_org_and_package(self):
        meta = {"auth": {"org": "splent-io", "package": "splent_feature_auth"}}
        result = _dep_spec_from_meta("auth", meta)
        assert result == "splent-io/splent_feature_auth"

    def test_package_only(self):
        meta = {"auth": {"package": "splent_feature_auth"}}
        result = _dep_spec_from_meta("auth", meta)
        assert result == "splent_feature_auth"

    def test_missing_feature_raises(self):
        with pytest.raises(click.ClickException, match="missing"):
            _dep_spec_from_meta("ghost", {})

    def test_missing_package_raises(self):
        meta = {"auth": {"org": "splent-io"}}
        with pytest.raises(click.ClickException, match="package"):
            _dep_spec_from_meta("auth", meta)


# ---------------------------------------------------------------------------
# Reading the current feature list from a parsed pyproject dict
#
# The hardening pass replaced fragile regex extraction of the features block
# (_extract_string_items) with proper TOML parsing. The reading responsibility
# now lives in read_features_from_data, so we test that instead of dead code.
# ---------------------------------------------------------------------------

class TestReadFeaturesFromData:
    def test_reads_canonical_location(self):
        data = {"tool": {"splent": {"features": ["ns/feat_a@v1.0.0", "ns/feat_b"]}}}
        items = read_features_from_data(data)
        assert "ns/feat_a@v1.0.0" in items
        assert "ns/feat_b" in items

    def test_reads_legacy_optional_dependencies(self):
        data = {
            "project": {"optional-dependencies": {"features": ["ns/feat_legacy"]}}
        }
        items = read_features_from_data(data)
        assert "ns/feat_legacy" in items

    def test_strips_whitespace_and_blanks(self):
        data = {"tool": {"splent": {"features": ["  ns/feat_a  ", "", "  "]}}}
        items = read_features_from_data(data)
        assert items == ["ns/feat_a"]

    def test_empty_data(self):
        assert read_features_from_data({}) == []

    def test_merges_env_specific(self):
        data = {
            "tool": {
                "splent": {
                    "features": ["base/feat"],
                    "features_dev": ["dev/feat"],
                }
            }
        }
        items = read_features_from_data(data, env="dev")
        assert items == ["base/feat", "dev/feat"]


# ---------------------------------------------------------------------------
# The merge logic the command applies before writing back
#
# Replaces the old regex-rewrite orchestration (_rewrite_pyproject_features_block):
# append missing specs, dedup, preserve order. This mirrors product_auto_require
# lines that build `new_features` from the current list + specs to add.
# ---------------------------------------------------------------------------

def _merge_features(current, to_add):
    """Mirror of the merge in product_auto_require.product_complete."""
    existing = set(current)
    new_features = list(current)
    for spec in to_add:
        if spec not in existing:
            new_features.append(spec)
            existing.add(spec)
    return new_features


class TestMergeFeatures:
    def test_adds_new_item(self):
        result = _merge_features(["ns/feat_a"], ["ns/feat_b"])
        assert "ns/feat_b" in result

    def test_preserves_existing_items(self):
        result = _merge_features(["ns/feat_a"], ["ns/feat_b"])
        assert "ns/feat_a" in result

    def test_no_duplicates_added(self):
        result = _merge_features(["ns/feat_a"], ["ns/feat_a"])
        assert result.count("ns/feat_a") == 1

    def test_empty_to_add_returns_unchanged(self):
        current = ["ns/feat_a"]
        result = _merge_features(current, [])
        assert result == current

    def test_multiple_new_items(self):
        result = _merge_features([], ["a/x", "b/y"])
        assert "a/x" in result
        assert "b/y" in result

    def test_order_preserved(self):
        result = _merge_features(["a", "b"], ["c", "a"])
        assert result == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Writing the feature list back into a parsed pyproject dict
#
# Replaces the old text rendering (_render_features_block). The hardening writes
# via write_features_to_data + tomli_w, so we test the round-trip here.
# ---------------------------------------------------------------------------

class TestWriteFeaturesToData:
    def test_writes_to_canonical_location(self):
        data = {}
        write_features_to_data(data, ["ns/feat"])
        assert data["tool"]["splent"]["features"] == ["ns/feat"]

    def test_round_trip(self):
        data = {}
        write_features_to_data(data, ["a", "b", "c"])
        assert read_features_from_data(data) == ["a", "b", "c"]

    def test_empty_items(self):
        data = {}
        write_features_to_data(data, [])
        assert data["tool"]["splent"]["features"] == []

    def test_removes_legacy_location(self):
        data = {
            "project": {"optional-dependencies": {"features": ["old/feat"]}}
        }
        write_features_to_data(data, ["new/feat"])
        assert "features" not in data["project"]["optional-dependencies"]
        assert read_features_from_data(data) == ["new/feat"]

    def test_serializes_with_tomli_w(self):
        import tomli_w
        import tomllib

        data = {}
        write_features_to_data(data, ["ns/feat_a", "ns/feat_b"])
        text = tomli_w.dumps(data)
        reparsed = tomllib.loads(text)
        assert read_features_from_data(reparsed) == ["ns/feat_a", "ns/feat_b"]
