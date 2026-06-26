"""Tests for product:env robust .env parsing (_parse_env_line).

Covers the hardened parsing behaviors: export prefix, quoted values,
values containing '#', blank/comment lines, and malformed/empty keys.
These are driven both via the pure helper and via reading real tmp .env
files line-by-line (the way the command consumes them).
"""

from splent_cli.commands.product.product_env import _parse_env_line


def _parse_file(path):
    """Mimic how product_env consumes a .env file: parse each line, skip None."""
    result = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parsed = _parse_env_line(line)
            if parsed is not None:
                k, v = parsed
                result[k] = v
    return result


class TestExportPrefix:
    def test_export_prefix_is_stripped(self):
        assert _parse_env_line("export KEY=val") == ("KEY", "val")

    def test_export_prefix_with_quoted_value(self):
        assert _parse_env_line('export KEY="val"') == ("KEY", "val")

    def test_export_tab_prefix_is_stripped(self):
        assert _parse_env_line("export\tKEY=val") == ("KEY", "val")

    def test_word_starting_with_export_is_not_treated_as_prefix(self):
        # "exported" is a legitimate key name, not the export keyword.
        assert _parse_env_line("exported=1") == ("exported", "1")


class TestQuotedValues:
    def test_double_quotes_stripped(self):
        assert _parse_env_line('KEY="hello world"') == ("KEY", "hello world")

    def test_single_quotes_stripped(self):
        assert _parse_env_line("KEY='hello world'") == ("KEY", "hello world")

    def test_unquoted_value_unchanged(self):
        assert _parse_env_line("KEY=hello") == ("KEY", "hello")

    def test_empty_quoted_value_becomes_empty_string(self):
        assert _parse_env_line('KEY=""') == ("KEY", "")

    def test_mismatched_quotes_not_stripped(self):
        # Leading " but trailing ' — not a matched pair, keep verbatim.
        assert _parse_env_line("KEY=\"oops'") == ("KEY", "\"oops'")


class TestHashInValues:
    def test_hash_kept_in_unquoted_value(self):
        # '#' is part of the value, not a comment delimiter, for real .env files.
        assert _parse_env_line("KEY=abc#123") == ("KEY", "abc#123")

    def test_hash_kept_in_quoted_value(self):
        assert _parse_env_line('PASSWORD="p#ss#word"') == (
            "PASSWORD",
            "p#ss#word",
        )

    def test_value_that_is_only_hashes(self):
        assert _parse_env_line("KEY=###") == ("KEY", "###")


class TestBlankAndCommentLines:
    def test_blank_line_skipped(self):
        assert _parse_env_line("") is None

    def test_whitespace_only_line_skipped(self):
        assert _parse_env_line("   \t  ") is None

    def test_full_line_comment_skipped(self):
        assert _parse_env_line("# this is a comment") is None

    def test_indented_comment_skipped(self):
        assert _parse_env_line("    # indented comment") is None


class TestMalformedKeys:
    def test_line_without_equals_skipped(self):
        assert _parse_env_line("JUST_A_WORD") is None

    def test_empty_key_skipped(self):
        assert _parse_env_line("=value") is None

    def test_export_only_empty_key_skipped(self):
        assert _parse_env_line("export =value") is None

    def test_key_with_space_skipped(self):
        # "bad key" is not a valid identifier → skipped, not a wrong pair.
        assert _parse_env_line("bad key=value") is None

    def test_key_with_special_char_skipped(self):
        assert _parse_env_line("KEY-NAME=value") is None

    def test_valid_key_with_underscore_and_digits(self):
        assert _parse_env_line("MY_KEY_2=value") == ("MY_KEY_2", "value")


class TestValueEdgeCases:
    def test_value_containing_equals(self):
        # Only the first '=' splits key/value; the rest stays in the value.
        assert _parse_env_line("KEY=a=b=c") == ("KEY", "a=b=c")

    def test_surrounding_whitespace_trimmed(self):
        assert _parse_env_line("  KEY = value  ") == ("KEY", "value")

    def test_empty_value(self):
        assert _parse_env_line("KEY=") == ("KEY", "")


class TestParseRealFile:
    def test_mixed_env_file_parses_correctly(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "# database config\n"
            "\n"
            "export DB_HOST=localhost\n"
            'DB_PASSWORD="p#ss#word"\n'
            "DB_PORT=5432\n"
            "   \n"
            "# trailing comment\n"
            "bad key=ignored\n"
            "=ignored_empty_key\n"
            "URL=http://x?a=1&b=2\n"
            "SINGLE='quoted value'\n"
        )
        parsed = _parse_file(str(env))
        assert parsed == {
            "DB_HOST": "localhost",
            "DB_PASSWORD": "p#ss#word",
            "DB_PORT": "5432",
            "URL": "http://x?a=1&b=2",
            "SINGLE": "quoted value",
        }

    def test_malformed_lines_do_not_create_wrong_pairs(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("JUST_A_WORD\n=novalue\nbad key=val\nGOOD=ok\n")
        parsed = _parse_file(str(env))
        # Only the one valid pair survives; no spurious keys.
        assert parsed == {"GOOD": "ok"}

    def test_later_line_overrides_earlier(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEY=first\nKEY=second\n")
        parsed = _parse_file(str(env))
        assert parsed == {"KEY": "second"}
