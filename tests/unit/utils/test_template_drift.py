"""Tests for pure helper functions in template_drift.py."""
import pytest
from pathlib import Path

from splent_cli.utils.template_drift import (
    _pascalcase,
    count_changed_lines,
    feature_ctx,
    file_diff,
    get_stored_cli_version,
    product_ctx,
    resolve_feature_rel,
    resolve_product_rel,
)


# ---------------------------------------------------------------------------
# _pascalcase
# ---------------------------------------------------------------------------

class TestPascalcase:
    def test_single_word(self):
        assert _pascalcase("auth") == "Auth"

    def test_snake_case(self):
        assert _pascalcase("my_product") == "MyProduct"

    def test_three_words(self):
        assert _pascalcase("splent_feature_auth") == "SplentFeatureAuth"

    def test_already_capitalized(self):
        assert _pascalcase("MyApp") == "Myapp"

    def test_empty_string(self):
        assert _pascalcase("") == ""


# ---------------------------------------------------------------------------
# product_ctx
# ---------------------------------------------------------------------------

class TestProductCtx:
    def test_contains_product_name(self):
        ctx = product_ctx("myapp")
        assert ctx["product_name"] == "myapp"

    def test_pascal_name_correct(self):
        ctx = product_ctx("my_app")
        assert ctx["pascal_name"] == "MyApp"

    def test_ports_are_integers(self):
        ctx = product_ctx("myapp")
        assert isinstance(ctx["web_port"], int)
        assert isinstance(ctx["db_port"], int)
        assert isinstance(ctx["redis_port"], int)

    def test_web_port_in_range(self):
        ctx = product_ctx("myapp")
        assert 5000 <= ctx["web_port"] < 6000

    def test_db_port_in_range(self):
        ctx = product_ctx("myapp")
        assert 33060 <= ctx["db_port"] < 34060

    def test_redis_port_in_range(self):
        ctx = product_ctx("myapp")
        assert 6379 <= ctx["redis_port"] < 7379

    def test_different_names_may_produce_different_ports(self):
        ctx1 = product_ctx("app_alpha")
        ctx2 = product_ctx("app_beta")
        # Not guaranteed to differ, but test they're valid
        assert isinstance(ctx1["web_port"], int)
        assert isinstance(ctx2["web_port"], int)

    def test_cli_version_present(self):
        ctx = product_ctx("myapp")
        assert "cli_version" in ctx


# ---------------------------------------------------------------------------
# feature_ctx
# ---------------------------------------------------------------------------

class TestFeatureCtx:
    def test_contains_feature_name(self):
        ctx = feature_ctx("splent_io", "auth")
        assert ctx["feature_name"] == "auth"

    def test_contains_org_safe(self):
        ctx = feature_ctx("splent_io", "auth")
        assert ctx["org_safe"] == "splent_io"

    def test_feature_import_format(self):
        ctx = feature_ctx("splent_io", "auth")
        assert ctx["feature_import"] == "splent_io.auth"

    def test_cli_version_present(self):
        ctx = feature_ctx("splent_io", "auth")
        assert "cli_version" in ctx


# ---------------------------------------------------------------------------
# file_diff
# ---------------------------------------------------------------------------

class TestFileDiff:
    def test_no_diff_when_contents_match(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello\n")
        assert file_diff(f, "hello\n") is None

    def test_returns_diff_when_contents_differ(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("old content\n")
        result = file_diff(f, "new content\n")
        assert result is not None
        assert isinstance(result, list)

    def test_returns_none_when_file_missing(self, tmp_path):
        assert file_diff(tmp_path / "missing.txt", "anything") is None

    def test_diff_contains_added_line(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("line1\n")
        result = file_diff(f, "line1\nline2\n")
        assert result is not None
        added = [l for l in result if l.startswith("+") and not l.startswith("+++")]
        assert any("line2" in l for l in added)


# ---------------------------------------------------------------------------
# count_changed_lines
# ---------------------------------------------------------------------------

class TestCountChangedLines:
    def test_counts_added_lines(self):
        diff = ["+++ b/file.txt\n", "+new line\n", "+another\n"]
        assert count_changed_lines(diff) == 2

    def test_counts_removed_lines(self):
        diff = ["--- a/file.txt\n", "-old line\n"]
        assert count_changed_lines(diff) == 1

    def test_excludes_header_lines(self):
        diff = ["+++ b/file.txt\n", "--- a/file.txt\n"]
        assert count_changed_lines(diff) == 0

    def test_empty_diff(self):
        assert count_changed_lines([]) == 0

    def test_mixed_diff(self):
        diff = [
            "--- a/f\n",
            "+++ b/f\n",
            "-removed\n",
            "+added\n",
            " context\n",
        ]
        assert count_changed_lines(diff) == 2


# ---------------------------------------------------------------------------
# get_stored_cli_version
# ---------------------------------------------------------------------------

class TestGetStoredCliVersion:
    def test_returns_version_when_present(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_bytes(b'[tool.splent]\ncli_version = "1.2.3"\n')
        assert get_stored_cli_version(p) == "1.2.3"

    def test_returns_none_when_key_missing(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_bytes(b'[tool.splent]\nother = "value"\n')
        assert get_stored_cli_version(p) is None

    def test_returns_none_when_file_missing(self, tmp_path):
        assert get_stored_cli_version(tmp_path / "missing.toml") is None

    def test_returns_none_on_invalid_toml(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text("not valid toml }{")
        assert get_stored_cli_version(p) is None


# ---------------------------------------------------------------------------
# resolve_product_rel
# ---------------------------------------------------------------------------

class TestResolveProductRel:
    def test_replaces_name_placeholder(self):
        result = resolve_product_rel("docker/Dockerfile.{name}.dev", "myapp")
        assert result == "docker/Dockerfile.myapp.dev"

    def test_no_placeholder_unchanged(self):
        result = resolve_product_rel("scripts/setup.sh", "myapp")
        assert result == "scripts/setup.sh"

    def test_multiple_occurrences(self):
        result = resolve_product_rel("{name}/{name}.txt", "foo")
        assert result == "foo/foo.txt"


# ---------------------------------------------------------------------------
# resolve_feature_rel
# ---------------------------------------------------------------------------

class TestResolveFeatureRel:
    def test_replaces_org_and_name(self):
        result = resolve_feature_rel("src/{org}/{name}/code.py", "splent_io", "auth")
        assert result == "src/splent_io/auth/code.py"

    def test_no_placeholders_unchanged(self):
        result = resolve_feature_rel(".gitignore", "splent_io", "auth")
        assert result == ".gitignore"

    def test_only_org_placeholder(self):
        result = resolve_feature_rel("src/{org}/base.py", "splent_io", "auth")
        assert result == "src/splent_io/base.py"
