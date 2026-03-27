"""Tests for pure helper functions in uvl_utils.py."""
import os
import pytest

import click

from splent_cli.commands.uvl.uvl_utils import (
    extract_implications_from_uvl_text,
    get_uvl_cfg,
    load_pyproject,
    normalize_feature_name,
    read_splent_app,
    resolve_uvlhub_raw_url,
    write_csvconf_full,
)


# ---------------------------------------------------------------------------
# normalize_feature_name
# ---------------------------------------------------------------------------

class TestNormalizeFeatureName:
    def test_full_versioned_dep(self):
        assert normalize_feature_name("splent_io/splent_feature_auth@v1.0.0") == "auth"

    def test_namespaced_no_version(self):
        assert normalize_feature_name("splent_io/splent_feature_profile") == "profile"

    def test_no_namespace_no_version(self):
        assert normalize_feature_name("splent_feature_notes") == "notes"

    def test_no_prefix_plain_name(self):
        assert normalize_feature_name("blog") == "blog"

    def test_strips_whitespace(self):
        assert normalize_feature_name("  splent_feature_auth  ") == "auth"

    def test_with_dashes_in_namespace(self):
        assert normalize_feature_name("my-org/splent_feature_shop@v2.0.0") == "shop"

    def test_invalid_name_raises(self):
        with pytest.raises(click.ClickException):
            normalize_feature_name("splent_io/splent_feature_@bad")


# ---------------------------------------------------------------------------
# resolve_uvlhub_raw_url
# ---------------------------------------------------------------------------

class TestResolveUvlhubRawUrl:
    def test_builds_correct_url(self):
        url = resolve_uvlhub_raw_url("uvlhub.io", "10.1234/test", "model.uvl")
        assert url == "https://www.uvlhub.io/doi/10.1234/test/files/raw/model.uvl/"

    def test_unsupported_mirror_raises(self):
        with pytest.raises(click.ClickException, match="Unsupported mirror"):
            resolve_uvlhub_raw_url("other.io", "10.1234/test", "model.uvl")

    def test_doi_with_slashes(self):
        url = resolve_uvlhub_raw_url("uvlhub.io", "10.99/my/doi", "app.uvl")
        assert "10.99/my/doi" in url
        assert url.endswith("/app.uvl/")


# ---------------------------------------------------------------------------
# extract_implications_from_uvl_text
# ---------------------------------------------------------------------------

class TestExtractImplicationsFromUvlText:
    def test_simple_implication(self):
        text = "profile => auth\n"
        result = extract_implications_from_uvl_text(text)
        assert ("profile", "auth") in result

    def test_ignores_comment_lines(self):
        text = "// this is a comment\nprofile => auth\n"
        result = extract_implications_from_uvl_text(text)
        assert len(result) == 1
        assert ("profile", "auth") in result

    def test_ignores_blank_lines(self):
        text = "\n\nprofile => auth\n\n"
        result = extract_implications_from_uvl_text(text)
        assert ("profile", "auth") in result

    def test_multiple_implications(self):
        text = "a => b\nb => c\n"
        result = extract_implications_from_uvl_text(text)
        assert ("a", "b") in result
        assert ("b", "c") in result

    def test_handles_html_entity(self):
        text = "profile =&gt; auth\n"
        result = extract_implications_from_uvl_text(text)
        assert ("profile", "auth") in result

    def test_ignores_non_implication_lines(self):
        text = "features\n    auth { org 'x' }\nconstraints\n"
        result = extract_implications_from_uvl_text(text)
        assert result == []

    def test_empty_text(self):
        assert extract_implications_from_uvl_text("") == []

    def test_extra_whitespace_around_arrow(self):
        text = "profile  =>  auth\n"
        result = extract_implications_from_uvl_text(text)
        assert ("profile", "auth") in result


# ---------------------------------------------------------------------------
# write_csvconf_full
# ---------------------------------------------------------------------------

class TestWriteCsvconfFull:
    def test_creates_file(self):
        path = write_csvconf_full(["A", "B", "C"], {"A", "C"})
        try:
            assert os.path.exists(path)
        finally:
            os.unlink(path)

    def test_selected_features_have_1(self):
        path = write_csvconf_full(["A", "B"], {"A"})
        try:
            content = open(path).read()
            assert "A,1" in content
            assert "B,0" in content
        finally:
            os.unlink(path)

    def test_empty_universe(self):
        path = write_csvconf_full([], set())
        try:
            assert open(path).read() == ""
        finally:
            os.unlink(path)

    def test_all_selected(self):
        path = write_csvconf_full(["X", "Y"], {"X", "Y"})
        try:
            content = open(path).read()
            assert "X,1" in content
            assert "Y,1" in content
        finally:
            os.unlink(path)

    def test_none_selected(self):
        path = write_csvconf_full(["X", "Y"], set())
        try:
            content = open(path).read()
            assert "X,0" in content
            assert "Y,0" in content
        finally:
            os.unlink(path)

    def test_file_suffix_is_csvconf(self):
        path = write_csvconf_full(["A"], {"A"})
        try:
            assert path.endswith(".csvconf")
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# read_splent_app
# ---------------------------------------------------------------------------

class TestReadSplentApp:
    def test_returns_app_name(self, tmp_path):
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        (tmp_path / ".env").write_text("SPLENT_APP=myapp\n")
        result = read_splent_app(str(tmp_path))
        assert result == "myapp"

    def test_missing_env_raises(self, tmp_path):
        with pytest.raises(click.ClickException, match="Missing"):
            read_splent_app(str(tmp_path))

    def test_missing_splent_app_raises(self, tmp_path):
        (tmp_path / ".env").write_text("OTHER_VAR=value\n")
        with pytest.raises(click.ClickException, match="SPLENT_APP"):
            read_splent_app(str(tmp_path))

    def test_product_dir_missing_raises(self, tmp_path):
        (tmp_path / ".env").write_text("SPLENT_APP=nonexistent\n")
        with pytest.raises(click.ClickException, match="not found"):
            read_splent_app(str(tmp_path))


# ---------------------------------------------------------------------------
# load_pyproject
# ---------------------------------------------------------------------------

class TestLoadPyproject:
    def test_loads_valid_toml(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_bytes(b'[project]\nname = "mypkg"\n')
        data = load_pyproject(str(p))
        assert data["project"]["name"] == "mypkg"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(click.ClickException, match="Missing"):
            load_pyproject(str(tmp_path / "missing.toml"))


# ---------------------------------------------------------------------------
# get_uvl_cfg
# ---------------------------------------------------------------------------

class TestGetUvlCfg:
    def test_extracts_uvl_section(self):
        data = {"tool": {"splent": {"uvl": {"file": "model.uvl"}}}}
        cfg = get_uvl_cfg(data)
        assert cfg["file"] == "model.uvl"

    def test_missing_section_raises(self):
        with pytest.raises(click.ClickException, match="Missing"):
            get_uvl_cfg({"tool": {}})

    def test_missing_tool_raises(self):
        with pytest.raises(click.ClickException):
            get_uvl_cfg({})
