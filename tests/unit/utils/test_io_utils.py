"""
Tests for splent_cli.utils.io_utils — safe filesystem helpers.

Covers the hardened behaviors:
  * load_toml / load_json raise a clean ClickException naming the file on
    missing / malformed input, and return a dict on valid input.
  * atomic_write writes content, leaves no temp file behind on success, and on
    a mid-write failure (os.replace raising) preserves the original file and
    removes the temp file.
  * backup_file returns None when the source is absent and produces a
    byte-identical ``.bak`` copy when present.

No docker / git / network / db: pure filesystem against pytest tmp_path.
"""

import click
import pytest

from splent_cli.utils import io_utils
from splent_cli.utils.io_utils import (
    atomic_write,
    backup_file,
    load_json,
    load_toml,
)


# --------------------------------------------------------------------------- #
# load_toml
# --------------------------------------------------------------------------- #
class TestLoadToml:
    def test_missing_file_raises_clickexception_naming_file(self, tmp_path):
        missing = tmp_path / "pyproject.toml"
        with pytest.raises(click.ClickException) as exc:
            load_toml(missing)
        assert str(missing) in exc.value.message
        assert "Traceback" not in exc.value.message

    def test_invalid_toml_raises_clickexception_naming_file(self, tmp_path):
        bad = tmp_path / "bad.toml"
        bad.write_text("this is = not valid = toml ][", encoding="utf-8")
        with pytest.raises(click.ClickException) as exc:
            load_toml(bad)
        assert str(bad) in exc.value.message
        assert "not valid TOML" in exc.value.message

    def test_valid_toml_returns_dict(self, tmp_path):
        good = tmp_path / "ok.toml"
        good.write_text('[tool]\nname = "splent"\nport = 8080\n', encoding="utf-8")
        result = load_toml(good)
        assert isinstance(result, dict)
        assert result["tool"]["name"] == "splent"
        assert result["tool"]["port"] == 8080

    def test_what_label_used_in_error(self, tmp_path):
        missing = tmp_path / "pyproject.toml"
        with pytest.raises(click.ClickException) as exc:
            load_toml(missing, what="pyproject")
        assert "pyproject" in exc.value.message


# --------------------------------------------------------------------------- #
# load_json
# --------------------------------------------------------------------------- #
class TestLoadJson:
    def test_missing_file_raises_clickexception_naming_file(self, tmp_path):
        missing = tmp_path / "config.json"
        with pytest.raises(click.ClickException) as exc:
            load_json(missing)
        assert str(missing) in exc.value.message

    def test_invalid_json_raises_clickexception_naming_file(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not: valid json, }", encoding="utf-8")
        with pytest.raises(click.ClickException) as exc:
            load_json(bad)
        assert str(bad) in exc.value.message
        assert "not valid JSON" in exc.value.message

    def test_valid_json_returns_dict(self, tmp_path):
        good = tmp_path / "ok.json"
        good.write_text('{"name": "splent", "count": 3}', encoding="utf-8")
        result = load_json(good)
        assert isinstance(result, dict)
        assert result == {"name": "splent", "count": 3}


# --------------------------------------------------------------------------- #
# atomic_write
# --------------------------------------------------------------------------- #
class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "nested" / "deep" / "out.txt"
        atomic_write(target, "content")
        assert target.read_text(encoding="utf-8") == "content"

    def test_no_temp_file_left_on_success(self, tmp_path):
        target = tmp_path / "out.txt"
        atomic_write(target, "data")
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "out.txt"]
        assert leftovers == []

    def test_overwrites_existing_atomically(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("old", encoding="utf-8")
        atomic_write(target, "new")
        assert target.read_text(encoding="utf-8") == "new"
        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "out.txt"]
        assert leftovers == []

    def test_failure_mid_write_preserves_original(self, tmp_path, monkeypatch):
        target = tmp_path / "out.txt"
        target.write_text("ORIGINAL", encoding="utf-8")

        def boom(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(io_utils.os, "replace", boom)

        with pytest.raises(OSError):
            atomic_write(target, "NEWCONTENT")

        # Original file untouched.
        assert target.read_text(encoding="utf-8") == "ORIGINAL"

    def test_failure_mid_write_leaves_no_temp_file(self, tmp_path, monkeypatch):
        target = tmp_path / "out.txt"
        target.write_text("ORIGINAL", encoding="utf-8")

        def boom(src, dst):
            raise OSError("simulated replace failure")

        monkeypatch.setattr(io_utils.os, "replace", boom)

        with pytest.raises(OSError):
            atomic_write(target, "NEWCONTENT")

        leftovers = [p.name for p in tmp_path.iterdir() if p.name != "out.txt"]
        assert leftovers == []


# --------------------------------------------------------------------------- #
# backup_file
# --------------------------------------------------------------------------- #
class TestBackupFile:
    def test_returns_none_when_source_absent(self, tmp_path):
        missing = tmp_path / "nope.env"
        assert backup_file(missing) is None
        assert not (tmp_path / "nope.env.bak").exists()

    def test_creates_byte_identical_backup(self, tmp_path):
        src = tmp_path / ".env"
        payload = b"TOKEN=secret\nbinary\x00\xff\n"
        src.write_bytes(payload)
        bak = backup_file(src)
        assert bak == tmp_path / ".env.bak"
        assert bak.read_bytes() == payload

    def test_custom_suffix(self, tmp_path):
        src = tmp_path / "pyproject.toml"
        src.write_text("data", encoding="utf-8")
        bak = backup_file(src, suffix=".orig")
        assert bak == tmp_path / "pyproject.toml.orig"
        assert bak.read_text(encoding="utf-8") == "data"
