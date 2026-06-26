"""Guard tests for splent_cli.utils.manifest._load.

Hardened behavior: a corrupt/truncated splent.manifest.json must NOT crash
manifest-touching commands with a raw json.JSONDecodeError. _load delegates to
io_utils.load_json which converts parse/IO failures into a clear
click.ClickException naming the file, and _load itself rejects a top-level
non-object (e.g. a JSON list/scalar) with a ClickException rather than letting a
later ``data["features"]`` access blow up with a raw AttributeError/TypeError.

Driven via real tmp_path manifest files (no docker/git/network needed).
"""

import json

import click
import pytest

from splent_cli.utils.manifest import (
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    read_manifest,
    set_feature_state,
    get_feature_state,
)


def _write_manifest(tmp_path, text):
    p = tmp_path / MANIFEST_FILENAME
    p.write_text(text, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Hardened: corrupt / truncated JSON -> clean ClickException, no raw decode err
# ---------------------------------------------------------------------------


def test_truncated_json_raises_clickexception_not_jsondecodeerror(tmp_path):
    # A half-written file (process killed mid _save).
    _write_manifest(tmp_path, '{"schema_version": "1", "features": {"acme/foo": {')

    with pytest.raises(click.ClickException) as exc:
        read_manifest(str(tmp_path))

    # No raw decode error escaped, and the message names the manifest file.
    assert not isinstance(exc.value, json.JSONDecodeError)
    assert MANIFEST_FILENAME in str(exc.value)


def test_garbage_json_raises_clean_clickexception(tmp_path):
    _write_manifest(tmp_path, "this is not json at all {{{")

    with pytest.raises(click.ClickException) as exc:
        read_manifest(str(tmp_path))

    msg = str(exc.value)
    assert "JSON" in msg
    # Clean operator-facing message, no traceback noise leaking through.
    assert "Traceback" not in msg


def test_empty_file_raises_clickexception(tmp_path):
    _write_manifest(tmp_path, "")

    with pytest.raises(click.ClickException):
        read_manifest(str(tmp_path))


def test_top_level_list_raises_malformed_clickexception(tmp_path):
    # Valid JSON, but not an object -> must be rejected cleanly, not crash on
    # data["features"] / data.get later.
    _write_manifest(tmp_path, json.dumps(["not", "an", "object"]))

    with pytest.raises(click.ClickException) as exc:
        read_manifest(str(tmp_path))

    assert "malformed" in str(exc.value).lower()


def test_top_level_scalar_raises_malformed_clickexception(tmp_path):
    _write_manifest(tmp_path, json.dumps(42))

    with pytest.raises(click.ClickException) as exc:
        read_manifest(str(tmp_path))

    assert MANIFEST_FILENAME in str(exc.value)


def test_corrupt_manifest_surfaces_through_mutating_api(tmp_path):
    # The guard must hold for write paths too, not only read_manifest.
    _write_manifest(tmp_path, "{ broken")

    with pytest.raises(click.ClickException):
        set_feature_state(
            str(tmp_path),
            "test_app",
            "acme/foo",
            "declared",
            namespace="acme",
            name="foo",
        )


# ---------------------------------------------------------------------------
# Self-healing of a non-dict "features" value (still valid object overall)
# ---------------------------------------------------------------------------


def test_non_dict_features_is_healed_not_an_error(tmp_path):
    _write_manifest(tmp_path, json.dumps({"schema_version": "1", "features": ["oops"]}))
    result = read_manifest(str(tmp_path))
    assert result["features"] == {}


# ---------------------------------------------------------------------------
# Happy path: valid manifest loads; missing file returns a scaffold
# ---------------------------------------------------------------------------


def test_missing_manifest_returns_empty_scaffold(tmp_path):
    result = read_manifest(str(tmp_path))
    assert result == {"schema_version": SCHEMA_VERSION, "features": {}}


def test_valid_manifest_loads_features(tmp_path):
    data = {
        "schema_version": "1",
        "product": "test_app",
        "features": {"acme/foo": {"name": "foo", "state": "active"}},
    }
    _write_manifest(tmp_path, json.dumps(data))

    result = read_manifest(str(tmp_path))
    assert result["features"]["acme/foo"]["state"] == "active"


def test_set_then_get_state_roundtrip(tmp_path):
    set_feature_state(
        str(tmp_path),
        "test_app",
        "acme/foo",
        "declared",
        namespace="acme",
        name="foo",
    )
    # File is now valid JSON and the state is readable back.
    assert get_feature_state(str(tmp_path), "acme/foo") == "declared"

    manifest = read_manifest(str(tmp_path))
    assert manifest["product"] == "test_app"
    assert manifest["schema_version"] == SCHEMA_VERSION
