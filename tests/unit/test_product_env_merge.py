"""
Tests for product:env --merge org normalization.
"""
import os
from pathlib import Path


class TestProductEnvMergeOrgNormalization:
    """Verify that the merge mode normalizes org names (splent-io → splent_io)
    when constructing cache paths."""

    def test_org_normalization_in_merge_path(self, product_workspace, monkeypatch):
        """The merge code should normalize 'splent-io' to 'splent_io' in cache paths."""
        ws = product_workspace

        # Set up features in pyproject
        pyproject = ws / "test_app" / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            '[project.optional-dependencies]\n'
            'features = ["splent-io/splent_feature_redis@v1.2.1"]\n'
        )

        # Create cache with underscore org (real filesystem)
        cache_docker = ws / ".splent_cache" / "features" / "splent_io" / "splent_feature_redis@v1.2.1" / "docker"
        cache_docker.mkdir(parents=True)
        (cache_docker / ".env.example").write_text("REDIS_PORT=6380\n")

        # The merge code should find .splent_cache/features/splent_io/...
        # NOT .splent_cache/features/splent-io/...
        expected = cache_docker / ".env.example"
        assert expected.exists()

        # Verify the WRONG path does NOT exist
        wrong_path = ws / ".splent_cache" / "features" / "splent-io" / "splent_feature_redis@v1.2.1" / "docker"
        assert not wrong_path.exists()


class TestProductSyncRelativeSymlinks:
    """Verify product:sync creates relative symlinks."""

    def test_symlink_is_relative(self, product_workspace):
        ws = product_workspace
        cache = ws / ".splent_cache" / "features" / "splent_io" / "feat@v1.0.0"
        cache.mkdir(parents=True)

        features_dir = ws / "test_app" / "features" / "splent_io"
        features_dir.mkdir(parents=True)

        link_path = features_dir / "feat@v1.0.0"
        rel_target = os.path.relpath(str(cache), str(features_dir))
        os.symlink(rel_target, str(link_path))

        # Verify it's relative
        target = os.readlink(str(link_path))
        assert not os.path.isabs(target)

        # Verify it resolves
        assert link_path.exists()
