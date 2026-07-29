"""
Tests for the feature-entry helpers in splent_cli.utils.feature_utils.

These are the single source of truth for "is this pyproject entry the same
feature?", where the namespace may be spelled as the GitHub org (splent-io) or
as the Python namespace (splent_io), and the entry may or may not be pinned.
"""

from splent_cli.utils.feature_utils import (
    drop_feature_entries,
    entry_matches_feature,
    find_feature_entries,
    read_feature_list,
    remove_feature_link,
)


def _data(features=None, dev=None, prod=None):
    """Build a parsed pyproject dict with the three feature lists."""
    splent = {}
    if features is not None:
        splent["features"] = features
    if dev is not None:
        splent["features_dev"] = dev
    if prod is not None:
        splent["features_prod"] = prod
    return {"project": {"name": "test_app"}, "tool": {"splent": splent}}


# ---------------------------------------------------------------------------
# entry_matches_feature
# ---------------------------------------------------------------------------


class TestEntryMatchesFeature:
    def test_dash_and_underscore_namespace_are_the_same(self):
        assert entry_matches_feature(
            "splent-io/splent_feature_theme", "splent_feature_theme", "splent_io"
        )
        assert entry_matches_feature(
            "splent_io/splent_feature_theme", "splent_feature_theme", "splent-io"
        )

    def test_version_is_ignored(self):
        assert entry_matches_feature(
            "splent-io/splent_feature_theme@v0.2.1", "splent_feature_theme", "splent_io"
        )

    def test_bare_entry_defaults_to_splent_io(self):
        assert entry_matches_feature(
            "splent_feature_theme", "splent_feature_theme", "splent_io"
        )

    def test_other_namespace_does_not_match(self):
        assert not entry_matches_feature(
            "drorganvidez/splent_feature_theme", "splent_feature_theme", "splent_io"
        )

    def test_other_feature_does_not_match(self):
        assert not entry_matches_feature(
            "splent-io/splent_feature_auth", "splent_feature_theme", "splent_io"
        )

    def test_namespace_none_matches_any_namespace(self):
        assert entry_matches_feature(
            "drorganvidez/splent_feature_theme", "splent_feature_theme"
        )


# ---------------------------------------------------------------------------
# find_feature_entries
# ---------------------------------------------------------------------------


class TestFindFeatureEntries:
    def test_finds_duplicate_across_spellings(self):
        data = _data(
            features=[
                "splent-io/splent_feature_theme",
                "splent_io/splent_feature_auth",
            ],
            dev=["splent_io/splent_feature_theme@v0.2.1"],
        )
        found = find_feature_entries(data, "splent_feature_theme", "splent_io")
        assert found == [
            ("features", "splent-io/splent_feature_theme"),
            ("features_dev", "splent_io/splent_feature_theme@v0.2.1"),
        ]

    def test_returns_empty_when_absent(self):
        data = _data(features=["splent-io/splent_feature_auth"])
        assert find_feature_entries(data, "splent_feature_theme", "splent_io") == []

    def test_reads_legacy_optional_dependencies(self):
        data = {
            "project": {
                "optional-dependencies": {
                    "features": ["splent-io/splent_feature_theme"]
                }
            }
        }
        found = find_feature_entries(data, "splent_feature_theme", "splent_io")
        assert found == [("features", "splent-io/splent_feature_theme")]


# ---------------------------------------------------------------------------
# drop_feature_entries
# ---------------------------------------------------------------------------


class TestDropFeatureEntries:
    def test_removes_every_spelling_from_every_list(self):
        data = _data(
            features=[
                "splent-io/splent_feature_theme",
                "splent-io/splent_feature_auth",
            ],
            dev=["splent_io/splent_feature_theme@v0.2.1"],
            prod=["splent_io/splent_feature_admin"],
        )
        removed = drop_feature_entries(data, "splent_feature_theme", "splent_io")

        assert removed == [
            ("features", "splent-io/splent_feature_theme"),
            ("features_dev", "splent_io/splent_feature_theme@v0.2.1"),
        ]
        assert read_feature_list(data, "features") == ["splent-io/splent_feature_auth"]
        assert read_feature_list(data, "features_dev") == []
        assert read_feature_list(data, "features_prod") == [
            "splent_io/splent_feature_admin"
        ]

    def test_keys_argument_restricts_the_search(self):
        data = _data(
            features=["splent-io/splent_feature_theme"],
            dev=["splent_io/splent_feature_theme@v0.2.1"],
        )
        removed = drop_feature_entries(
            data, "splent_feature_theme", "splent_io", keys=("features_dev",)
        )

        assert removed == [("features_dev", "splent_io/splent_feature_theme@v0.2.1")]
        assert read_feature_list(data, "features") == ["splent-io/splent_feature_theme"]

    def test_untouched_when_feature_absent(self):
        data = _data(features=["splent-io/splent_feature_auth"])
        assert drop_feature_entries(data, "splent_feature_theme", "splent_io") == []
        assert read_feature_list(data, "features") == ["splent-io/splent_feature_auth"]

    def test_migrates_legacy_location_on_removal(self):
        data = {
            "project": {
                "optional-dependencies": {
                    "features": [
                        "splent-io/splent_feature_theme",
                        "splent-io/splent_feature_auth",
                    ]
                }
            }
        }
        drop_feature_entries(data, "splent_feature_theme", "splent_io")

        assert data["tool"]["splent"]["features"] == ["splent-io/splent_feature_auth"]
        assert "features" not in data["project"]["optional-dependencies"]


# ---------------------------------------------------------------------------
# remove_feature_link
# ---------------------------------------------------------------------------


class TestRemoveFeatureLink:
    def _link(self, tmp_path, leaf, ns="splent_io"):
        target = tmp_path / "src_feature"
        target.mkdir(exist_ok=True)
        link_dir = tmp_path / "test_app" / "features" / ns
        link_dir.mkdir(parents=True, exist_ok=True)
        link = link_dir / leaf
        link.symlink_to(target)
        return link

    def test_removes_editable_link(self, tmp_path):
        link = self._link(tmp_path, "splent_feature_theme")
        assert remove_feature_link(
            str(tmp_path / "test_app"), "splent-io", "splent_feature_theme"
        )
        assert not link.is_symlink()

    def test_removes_versioned_link(self, tmp_path):
        link = self._link(tmp_path, "splent_feature_theme@v0.2.1")
        assert remove_feature_link(
            str(tmp_path / "test_app"), "splent_io", "splent_feature_theme", "v0.2.1"
        )
        assert not link.is_symlink()

    def test_returns_false_when_missing(self, tmp_path):
        assert not remove_feature_link(
            str(tmp_path / "test_app"), "splent_io", "splent_feature_ghost"
        )

    def test_leaves_real_directories_alone(self, tmp_path):
        real = tmp_path / "test_app" / "features" / "splent_io" / "splent_feature_theme"
        real.mkdir(parents=True)
        assert not remove_feature_link(
            str(tmp_path / "test_app"), "splent_io", "splent_feature_theme"
        )
        assert real.is_dir()
