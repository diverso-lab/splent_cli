"""
Tests for splent_cli.utils.manifest

The manifest utility is pure I/O + logic — no CLI invocation needed.
"""

import json
from pathlib import Path
import pytest

from splent_cli.utils.manifest import (
    feature_key,
    set_feature_state,
    remove_feature,
    read_manifest,
    manifest_exists,
    MANIFEST_FILENAME,
    VALID_STATES,
)


# ---------------------------------------------------------------------------
# feature_key
# ---------------------------------------------------------------------------

class TestFeatureKey:
    def test_editable_no_version(self):
        assert feature_key("splent_io", "splent_feature_auth") == "splent_io/splent_feature_auth"

    def test_pinned_with_version(self):
        assert feature_key("splent_io", "splent_feature_auth", "v1.1.1") == "splent_io/splent_feature_auth@v1.1.1"

    def test_dash_namespace_normalised(self):
        assert feature_key("splent-io", "splent_feature_auth") == "splent_io/splent_feature_auth"

    def test_version_none_gives_no_at(self):
        assert "@" not in feature_key("splent_io", "my_feature", None)


# ---------------------------------------------------------------------------
# manifest_exists
# ---------------------------------------------------------------------------

class TestManifestExists:
    def test_false_when_no_file(self, tmp_path):
        assert manifest_exists(str(tmp_path)) is False

    def test_true_when_file_present(self, tmp_path):
        (tmp_path / MANIFEST_FILENAME).write_text("{}")
        assert manifest_exists(str(tmp_path)) is True


# ---------------------------------------------------------------------------
# read_manifest
# ---------------------------------------------------------------------------

class TestReadManifest:
    def test_returns_empty_scaffold_when_missing(self, tmp_path):
        data = read_manifest(str(tmp_path))
        assert data["features"] == {}
        assert "schema_version" in data

    def test_reads_existing_file(self, tmp_path):
        content = {"schema_version": "1", "features": {"k": {"state": "active"}}}
        (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(content))
        data = read_manifest(str(tmp_path))
        assert data["features"]["k"]["state"] == "active"


# ---------------------------------------------------------------------------
# set_feature_state
# ---------------------------------------------------------------------------

class TestSetFeatureState:
    def _add(self, tmp_path, state="declared", version=None, mode="editable"):
        key = feature_key("splent_io", "my_feature", version)
        set_feature_state(
            str(tmp_path), "my_product", key, state,
            namespace="splent_io", name="my_feature",
            version=version, mode=mode,
        )
        return key

    def test_creates_manifest_file(self, tmp_path):
        self._add(tmp_path)
        assert (tmp_path / MANIFEST_FILENAME).exists()

    def test_entry_has_correct_state(self, tmp_path):
        key = self._add(tmp_path, "declared")
        data = read_manifest(str(tmp_path))
        assert data["features"][key]["state"] == "declared"

    def test_product_name_written(self, tmp_path):
        self._add(tmp_path)
        data = read_manifest(str(tmp_path))
        assert data["product"] == "my_product"

    def test_mode_pinned_with_version(self, tmp_path):
        key = self._add(tmp_path, version="v1.0.0", mode="pinned")
        data = read_manifest(str(tmp_path))
        assert data["features"][key]["mode"] == "pinned"
        assert data["features"][key]["version"] == "v1.0.0"

    def test_installed_at_set_on_installed_state(self, tmp_path):
        key = self._add(tmp_path, "installed")
        data = read_manifest(str(tmp_path))
        assert data["features"][key]["installed_at"] is not None

    def test_migrated_at_set_on_migrated_state(self, tmp_path):
        key = self._add(tmp_path, "migrated")
        data = read_manifest(str(tmp_path))
        assert data["features"][key]["migrated_at"] is not None

    def test_installed_at_not_set_for_declared(self, tmp_path):
        key = self._add(tmp_path, "declared")
        data = read_manifest(str(tmp_path))
        assert data["features"][key]["installed_at"] is None

    def test_declared_at_preserved_on_state_advance(self, tmp_path):
        key = self._add(tmp_path, "declared")
        first_declared_at = read_manifest(str(tmp_path))["features"][key]["declared_at"]
        # Advance to installed
        set_feature_state(
            str(tmp_path), "my_product", key, "installed",
            namespace="splent_io", name="my_feature",
        )
        data = read_manifest(str(tmp_path))
        assert data["features"][key]["declared_at"] == first_declared_at

    def test_invalid_state_raises(self, tmp_path):
        key = feature_key("splent_io", "my_feature")
        with pytest.raises(ValueError, match="Unknown state"):
            set_feature_state(
                str(tmp_path), "my_product", key, "flying",
                namespace="splent_io", name="my_feature",
            )

    def test_multiple_features_coexist(self, tmp_path):
        key_a = feature_key("splent_io", "feature_a")
        key_b = feature_key("splent_io", "feature_b")
        set_feature_state(str(tmp_path), "prod", key_a, "declared",
                          namespace="splent_io", name="feature_a")
        set_feature_state(str(tmp_path), "prod", key_b, "active",
                          namespace="splent_io", name="feature_b")
        data = read_manifest(str(tmp_path))
        assert data["features"][key_a]["state"] == "declared"
        assert data["features"][key_b]["state"] == "active"

    def test_updated_at_present(self, tmp_path):
        key = self._add(tmp_path)
        data = read_manifest(str(tmp_path))
        assert "updated_at" in data
        assert "updated_at" in data["features"][key]

    def test_all_valid_states_accepted(self, tmp_path):
        for state in VALID_STATES:
            key = feature_key("splent_io", f"feature_{state}")
            set_feature_state(
                str(tmp_path), "prod", key, state,
                namespace="splent_io", name=f"feature_{state}",
            )
        data = read_manifest(str(tmp_path))
        for state in VALID_STATES:
            key = feature_key("splent_io", f"feature_{state}")
            assert data["features"][key]["state"] == state


# ---------------------------------------------------------------------------
# remove_feature
# ---------------------------------------------------------------------------

class TestRemoveFeature:
    def test_removes_existing_entry(self, tmp_path):
        key = feature_key("splent_io", "my_feature")
        set_feature_state(str(tmp_path), "prod", key, "declared",
                          namespace="splent_io", name="my_feature")
        remove_feature(str(tmp_path), "prod", key)
        data = read_manifest(str(tmp_path))
        assert key not in data["features"]

    def test_no_error_when_key_missing(self, tmp_path):
        key = feature_key("splent_io", "ghost")
        # Should not raise even if the key was never in the manifest
        remove_feature(str(tmp_path), "prod", key)

    def test_only_target_removed(self, tmp_path):
        key_a = feature_key("splent_io", "keep_me")
        key_b = feature_key("splent_io", "remove_me")
        for k, n in [(key_a, "keep_me"), (key_b, "remove_me")]:
            set_feature_state(str(tmp_path), "prod", k, "declared",
                              namespace="splent_io", name=n)
        remove_feature(str(tmp_path), "prod", key_b)
        data = read_manifest(str(tmp_path))
        assert key_a in data["features"]
        assert key_b not in data["features"]
