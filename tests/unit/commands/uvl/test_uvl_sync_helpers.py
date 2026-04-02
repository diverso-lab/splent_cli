"""Tests for pure helper functions in uvl_sync.py."""
import pytest

from splent_cli.commands.product.product_auto_require import (
    _build_req_graph,
    _closure_requires,
    _dep_spec_from_meta,
    _extract_string_items,
    _parse_feature_metadata_from_uvl_text,
    _render_features_block,
    _rewrite_pyproject_features_block,
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
# _extract_string_items
# ---------------------------------------------------------------------------

class TestExtractStringItems:
    def test_double_quoted(self):
        body = '"ns/feat_a@v1.0.0",\n"ns/feat_b",\n'
        items = _extract_string_items(body)
        assert "ns/feat_a@v1.0.0" in items
        assert "ns/feat_b" in items

    def test_single_quoted(self):
        body = "'ns/feat_a',\n"
        items = _extract_string_items(body)
        assert "ns/feat_a" in items

    def test_strips_inline_hash_comments(self):
        body = '"ns/feat_a", # some comment\n'
        items = _extract_string_items(body)
        assert "ns/feat_a" in items
        assert "some comment" not in items

    def test_strips_inline_slash_comments(self):
        body = '"ns/feat_b", // other comment\n'
        items = _extract_string_items(body)
        assert "ns/feat_b" in items

    def test_empty_body(self):
        assert _extract_string_items("") == []


# ---------------------------------------------------------------------------
# _render_features_block
# ---------------------------------------------------------------------------

class TestRenderFeaturesBlock:
    def test_single_item(self):
        out = _render_features_block(["ns/feat"], indent_key="", indent_item="    ")
        assert "ns/feat" in out
        assert "features = [" in out

    def test_multiple_items(self):
        out = _render_features_block(["a", "b", "c"], indent_key="", indent_item="    ")
        assert '"a"' in out
        assert '"b"' in out
        assert '"c"' in out

    def test_indentation_applied(self):
        out = _render_features_block(["x"], indent_key="  ", indent_item="      ")
        assert out.startswith("  features = [")

    def test_empty_items(self):
        out = _render_features_block([], indent_key="", indent_item="    ")
        assert "features = [" in out
        assert "]" in out


# ---------------------------------------------------------------------------
# _rewrite_pyproject_features_block
# ---------------------------------------------------------------------------

class TestRewritePyprojectFeaturesBlock:
    def _make_pyproject(self, features):
        items = "\n".join(f'    "{f}",' for f in features)
        return (
            "[project.optional-dependencies]\n"
            "features = [\n"
            f"{items}\n"
            "]\n"
        )

    def test_adds_new_item(self):
        text = self._make_pyproject(["ns/feat_a"])
        result = _rewrite_pyproject_features_block(text, ["ns/feat_b"])
        assert "ns/feat_b" in result

    def test_preserves_existing_items(self):
        text = self._make_pyproject(["ns/feat_a"])
        result = _rewrite_pyproject_features_block(text, ["ns/feat_b"])
        assert "ns/feat_a" in result

    def test_no_duplicates_added(self):
        text = self._make_pyproject(["ns/feat_a"])
        result = _rewrite_pyproject_features_block(text, ["ns/feat_a"])
        assert result.count("ns/feat_a") == 1

    def test_empty_to_add_returns_unchanged(self):
        text = self._make_pyproject(["ns/feat_a"])
        result = _rewrite_pyproject_features_block(text, [])
        assert result == text

    def test_no_features_block_raises(self):
        text = "[project]\nname = 'foo'\n"
        with pytest.raises(ValueError, match="Cannot find"):
            _rewrite_pyproject_features_block(text, ["ns/feat"])

    def test_multiple_new_items(self):
        text = self._make_pyproject([])
        result = _rewrite_pyproject_features_block(text, ["a/x", "b/y"])
        assert "a/x" in result
        assert "b/y" in result
