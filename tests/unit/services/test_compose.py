"""
Tests for splent_cli.services.compose

Pure Python functions — no mocking needed for most cases.
Only resolve_file() touches the filesystem, so we use tmp_path there.
"""
import pytest
from pathlib import Path

from splent_cli.services import compose


# ---------------------------------------------------------------------------
# project_name()
# ---------------------------------------------------------------------------

class TestProjectName:
    def test_basic(self):
        assert compose.project_name("my_app", "dev") == "my_app_dev"

    def test_prod(self):
        assert compose.project_name("my_app", "prod") == "my_app_prod"

    def test_slash_replaced(self):
        assert compose.project_name("splent_io/auth", "dev") == "splent_io_auth_dev"

    def test_at_replaced(self):
        assert compose.project_name("auth@v1.0.0", "dev") == "auth_v1_0_0_dev"

    def test_dot_replaced(self):
        assert compose.project_name("auth.feature", "dev") == "auth_feature_dev"

    def test_combined_special_chars(self):
        result = compose.project_name("splent_io/auth@v1.0", "prod")
        assert "/" not in result
        assert "@" not in result
        assert "." not in result


# ---------------------------------------------------------------------------
# normalize_feature_ref()
# ---------------------------------------------------------------------------

class TestNormalizeFeatureRef:
    def test_bare_name_gets_default_namespace(self):
        assert compose.normalize_feature_ref("splent_feature_auth") == "splent_io/splent_feature_auth"

    def test_namespaced_unchanged(self):
        assert compose.normalize_feature_ref("splent_io/splent_feature_auth") == "splent_io/splent_feature_auth"

    def test_strips_features_prefix(self):
        result = compose.normalize_feature_ref("features/splent_io/splent_feature_auth")
        assert result == "splent_io/splent_feature_auth"

    def test_versioned_ref_with_namespace(self):
        result = compose.normalize_feature_ref("splent_io/splent_feature_auth@v1.0")
        assert result == "splent_io/splent_feature_auth@v1.0"


# ---------------------------------------------------------------------------
# resolve_file() — touches the filesystem
# ---------------------------------------------------------------------------

class TestResolveFile:
    def test_prefers_env_specific_file(self, tmp_path):
        docker_dir = tmp_path / "my_app" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / "docker-compose.dev.yml").touch()
        (docker_dir / "docker-compose.yml").touch()

        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is not None
        assert "docker-compose.dev.yml" in result

    def test_falls_back_to_generic(self, tmp_path):
        docker_dir = tmp_path / "my_app" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / "docker-compose.yml").touch()

        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is not None
        assert "docker-compose.yml" in result

    def test_returns_none_when_no_file(self, tmp_path):
        (tmp_path / "my_app" / "docker").mkdir(parents=True)
        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is None

    def test_returns_none_when_no_docker_dir(self, tmp_path):
        (tmp_path / "my_app").mkdir()
        result = compose.resolve_file(str(tmp_path / "my_app"), "dev")
        assert result is None


# ---------------------------------------------------------------------------
# product_path() and feature_docker_dir()
# ---------------------------------------------------------------------------

class TestPaths:
    def test_product_path(self):
        result = compose.product_path("my_app", "/workspace")
        assert result == "/workspace/my_app"

    def test_feature_docker_dir(self):
        result = compose.feature_docker_dir("/workspace", "splent_io/splent_feature_auth")
        assert result == "/workspace/.splent_cache/features/splent_io/splent_feature_auth/docker"
