"""Tests for manifest._load — schema validation (features must be dict)."""
import json
from pathlib import Path
from splent_cli.utils.manifest import read_manifest, MANIFEST_FILENAME


class TestManifestLoad:
    def test_returns_scaffold_when_file_missing(self, tmp_path):
        result = read_manifest(str(tmp_path))
        assert result["features"] == {}

    def test_returns_features_when_valid(self, tmp_path):
        data = {"schema_version": "1", "features": {"k": {"state": "declared"}}}
        (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(data))
        result = read_manifest(str(tmp_path))
        assert "k" in result["features"]

    def test_replaces_non_dict_features_with_empty_dict(self, tmp_path):
        """If 'features' key is a list (or other non-dict), _load should heal it."""
        bad = {"schema_version": "1", "features": ["broken"]}
        (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(bad))
        result = read_manifest(str(tmp_path))
        assert result["features"] == {}

    def test_replaces_null_features_with_empty_dict(self, tmp_path):
        bad = {"schema_version": "1", "features": None}
        (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(bad))
        result = read_manifest(str(tmp_path))
        assert result["features"] == {}
