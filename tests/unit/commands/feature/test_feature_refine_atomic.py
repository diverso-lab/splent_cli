"""Tests for feature:refine — the hardened pyproject.toml rewrite path.

Focus (hardened behaviors first):
  * The rewrite is atomic + backed up and the result re-parses as valid TOML
    (no corruption / truncation).
  * A malformed input pyproject.toml produces a clean error naming the file
    (no traceback) and never touches the file.

The command is an interactive wizard; we drive it end-to-end through the write
step with CliRunner ``input=`` (selecting the base feature, one model to extend,
and confirming the write). Heavy downstream work that needs real scanning is the
contract update — patched to a no-op. Auto-add to the product fails gracefully
on its own (the command catches it), so it does not need patching.

No docker / git / network: everything is filesystem + monkeypatched env.
"""

import os
import tomllib
from glob import glob
from unittest.mock import patch

from click.testing import CliRunner

from splent_cli.commands.feature.feature_refine import feature_refinement


# ── Fixtures / helpers ─────────────────────────────────────────────────────


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _build_workspace(tmp_path, refiner_pyproject: str):
    """Build a minimal workspace: a product listing a base feature (with an
    extensible ``models`` contract) and the refiner feature. ``refiner_pyproject``
    is written verbatim so callers can inject malformed content.

    Returns the absolute path to the refiner's pyproject.toml.
    """
    ws = str(tmp_path)
    product = "test_app"

    _write(
        os.path.join(ws, product, "pyproject.toml"),
        "[project]\n"
        "name = 'test_app'\n"
        "version = '0.1.0'\n\n"
        "[tool.splent]\n"
        "features = ['splent_io/splent_feature_notes', "
        "'splent_io/splent_feature_notes_tags']\n",
    )

    # Base feature (editable, at workspace root) with an extensible model.
    base = os.path.join(ws, "splent_feature_notes")
    os.makedirs(
        os.path.join(base, "src", "splent_io", "splent_feature_notes"),
        exist_ok=True,
    )
    _write(
        os.path.join(base, "pyproject.toml"),
        "[project]\n"
        "name = 'splent_feature_notes'\n"
        "version = '0.1.0'\n\n"
        "[tool.splent]\n"
        "namespace = 'splent_io'\n\n"
        "[tool.splent.contract.extensible]\n"
        "models = ['Notes']\n"
        "services = []\n"
        "templates = []\n"
        "hooks = []\n"
        "routes = false\n",
    )

    # Refiner feature (editable, at workspace root).
    refiner = os.path.join(ws, "splent_feature_notes_tags")
    os.makedirs(
        os.path.join(refiner, "src", "splent_io", "splent_feature_notes_tags"),
        exist_ok=True,
    )
    refiner_pp = os.path.join(refiner, "pyproject.toml")
    _write(refiner_pp, refiner_pyproject)
    return refiner_pp


def _valid_refiner_pyproject() -> str:
    return (
        "[project]\n"
        "name = 'splent_feature_notes_tags'\n"
        "version = '0.1.0'\n\n"
        "[tool.splent]\n"
        "namespace = 'splent_io'\n"
    )


def _invoke_refine(monkeypatch, tmp_path, input_text):
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")
    runner = CliRunner(mix_stderr=False)
    # update_contract shells out / scans real source dirs; stub it. The lazy
    # import inside the command resolves to this module attribute.
    with patch("splent_cli.commands.feature.feature_contract.update_contract"):
        return runner.invoke(
            feature_refinement,
            ["splent_feature_notes_tags"],
            input=input_text,
        )


# Prompt sequence to reach (and confirm) the write:
#   "1"  -> Step 1: select the only extensible base feature
#   "1"  -> Models multi-select: extend the only model
#   ""   -> Step 3: confirm write (default Y)
_WRITE_INPUT = "1\n1\n\n"


# ── Hardened: atomic + backed up + valid TOML ──────────────────────────────


class TestRewriteIsAtomicAndValid:
    def test_result_reparses_as_valid_toml(self, tmp_path, monkeypatch):
        refiner_pp = _build_workspace(tmp_path, _valid_refiner_pyproject())

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code == 0, result.output
        with open(refiner_pp, "rb") as f:
            data = tomllib.load(f)  # must not raise
        # The refinement section was actually written and is well-formed.
        refinement = data["tool"]["splent"]["refinement"]
        assert refinement["refines"] == "splent_feature_notes"

    def test_original_content_preserved(self, tmp_path, monkeypatch):
        refiner_pp = _build_workspace(tmp_path, _valid_refiner_pyproject())

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code == 0, result.output
        with open(refiner_pp, "rb") as f:
            data = tomllib.load(f)
        # The append must not clobber the pre-existing [project] / [tool.splent].
        assert data["project"]["name"] == "splent_feature_notes_tags"
        assert data["tool"]["splent"]["namespace"] == "splent_io"

    def test_backup_created_with_original_content(self, tmp_path, monkeypatch):
        original = _valid_refiner_pyproject()
        refiner_pp = _build_workspace(tmp_path, original)

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code == 0, result.output
        bak = refiner_pp + ".bak"
        assert os.path.isfile(bak), "expected a .bak backup beside pyproject.toml"
        with open(bak, "r", encoding="utf-8") as f:
            assert f.read() == original

    def test_no_temp_file_left_behind(self, tmp_path, monkeypatch):
        refiner_pp = _build_workspace(tmp_path, _valid_refiner_pyproject())

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code == 0, result.output
        # atomic_write writes ".<name>.*.tmp" then os.replace; nothing should remain.
        leftovers = glob(
            os.path.join(os.path.dirname(refiner_pp), ".pyproject.toml*.tmp")
        )
        assert leftovers == [], f"atomic write left temp files: {leftovers}"

    def test_success_message_and_no_traceback(self, tmp_path, monkeypatch):
        _build_workspace(tmp_path, _valid_refiner_pyproject())

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code == 0, result.output
        assert "pyproject.toml updated" in result.output
        assert "Traceback" not in result.output
        assert "TOMLDecodeError" not in result.output


# ── Hardened: malformed input -> clean error naming the file ───────────────


class TestMalformedInputPyproject:
    def test_clean_error_names_the_file(self, tmp_path, monkeypatch):
        _build_workspace(tmp_path, "[project\nname = = broken")

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code != 0
        # Clean ClickException to stderr (mix_stderr=False), not the wizard output.
        assert "not valid TOML" in result.stderr
        # The error must name the offending file.
        assert "splent_feature_notes_tags" in result.stderr
        assert "pyproject.toml" in result.stderr

    def test_no_traceback_on_malformed_input(self, tmp_path, monkeypatch):
        _build_workspace(tmp_path, "[project\nname = = broken")

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code != 0
        assert "Traceback" not in result.stderr
        assert "Traceback" not in result.output
        assert "TOMLDecodeError" not in result.stderr

    def test_malformed_input_left_untouched(self, tmp_path, monkeypatch):
        broken = "[project\nname = = broken"
        refiner_pp = _build_workspace(tmp_path, broken)

        result = _invoke_refine(monkeypatch, tmp_path, _WRITE_INPUT)

        assert result.exit_code != 0
        # The file must not be partially rewritten/truncated on the error path.
        with open(refiner_pp, "r", encoding="utf-8") as f:
            assert f.read() == broken
        # And no stray backup/temp artifacts from a write that never happened.
        assert not os.path.isfile(refiner_pp + ".bak")
        leftovers = glob(
            os.path.join(os.path.dirname(refiner_pp), ".pyproject.toml*.tmp")
        )
        assert leftovers == []


# ── Core happy path: missing feature / missing pyproject ───────────────────


class TestResolutionErrors:
    def test_refiner_not_found_clean_exit(self, tmp_path, monkeypatch):
        # Build a product but do NOT create the named refiner feature dir.
        _write(
            os.path.join(str(tmp_path), "test_app", "pyproject.toml"),
            "[project]\nname = 'test_app'\nversion = '0.1.0'\n\n"
            "[tool.splent]\nfeatures = []\n",
        )
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        runner = CliRunner(mix_stderr=False)

        result = runner.invoke(feature_refinement, ["splent_feature_ghost"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()
        assert "Traceback" not in result.output

    def test_refiner_missing_pyproject_clean_exit(self, tmp_path, monkeypatch):
        ws = str(tmp_path)
        _write(
            os.path.join(ws, "test_app", "pyproject.toml"),
            "[project]\nname = 'test_app'\nversion = '0.1.0'\n\n"
            "[tool.splent]\nfeatures = []\n",
        )
        # Refiner dir exists but has no pyproject.toml.
        os.makedirs(os.path.join(ws, "splent_feature_orphan"), exist_ok=True)
        monkeypatch.setenv("WORKING_DIR", ws)
        monkeypatch.setenv("SPLENT_APP", "test_app")
        runner = CliRunner(mix_stderr=False)

        result = runner.invoke(feature_refinement, ["splent_feature_orphan"])

        assert result.exit_code == 1
        assert "pyproject.toml not found" in result.output
        assert "Traceback" not in result.output
