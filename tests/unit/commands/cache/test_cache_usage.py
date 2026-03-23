"""
Tests for the cache:usage command.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.cache.cache_usage import cache_usage, _get_feature_usage


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_product(workspace, name, features=None):
    product_dir = workspace / name
    product_dir.mkdir(parents=True, exist_ok=True)
    features_list = "\n".join(f'  "{f}",' for f in (features or []))
    (product_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\n\n'
        f'[project.optional-dependencies]\nfeatures = [\n{features_list}\n]\n'
    )


# ---------------------------------------------------------------------------
# _get_feature_usage() helper
# ---------------------------------------------------------------------------

class TestGetFeatureUsage:
    def test_returns_empty_for_no_products(self, tmp_path):
        assert dict(_get_feature_usage(tmp_path)) == {}

    def test_maps_feature_to_product(self, tmp_path):
        _make_product(tmp_path, "app", ["splent_io/auth@v1.0.0"])
        usage = _get_feature_usage(tmp_path)
        assert "auth@v1.0.0" in usage
        assert "app" in usage["auth@v1.0.0"]

    def test_strips_namespace_prefix(self, tmp_path):
        _make_product(tmp_path, "app", ["ns/feature_a@v1"])
        usage = _get_feature_usage(tmp_path)
        assert "feature_a@v1" in usage

    def test_multiple_products_use_same_feature(self, tmp_path):
        _make_product(tmp_path, "app1", ["ns/auth@v1"])
        _make_product(tmp_path, "app2", ["ns/auth@v1"])
        usage = _get_feature_usage(tmp_path)
        assert len(usage["auth@v1"]) == 2

    def test_ignores_hidden_dirs(self, tmp_path):
        _make_product(tmp_path, ".splent_cache", ["ns/auth@v1"])
        usage = _get_feature_usage(tmp_path)
        assert "auth@v1" not in usage


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCacheUsageCommand:
    def test_no_features_shows_info(self, runner, workspace):
        result = runner.invoke(cache_usage, [])
        assert result.exit_code == 0
        assert "No features" in result.output

    def test_shows_feature_ref(self, runner, workspace):
        _make_product(workspace, "test_app", ["splent_io/splent_feature_auth@v1.0.0"])
        result = runner.invoke(cache_usage, [])
        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output

    def test_shows_product_name(self, runner, workspace):
        _make_product(workspace, "test_app", ["splent_io/splent_feature_auth@v1.0.0"])
        result = runner.invoke(cache_usage, [])
        assert "test_app" in result.output

    def test_filter_matching_feature(self, runner, workspace):
        _make_product(workspace, "test_app", ["ns/auth@v1", "ns/payments@v2"])
        result = runner.invoke(cache_usage, ["--feature", "auth"])
        assert result.exit_code == 0
        assert "auth" in result.output
        assert "payments" not in result.output

    def test_filter_no_match_shows_warning(self, runner, workspace):
        _make_product(workspace, "test_app", ["ns/auth@v1"])
        result = runner.invoke(cache_usage, ["--feature", "nonexistent"])
        assert result.exit_code == 0
        assert "No features matching" in result.output

    def test_multiple_products_listed(self, runner, workspace):
        _make_product(workspace, "app1", ["ns/auth@v1"])
        _make_product(workspace, "app2", ["ns/auth@v1"])
        result = runner.invoke(cache_usage, [])
        assert "app1" in result.output
        assert "app2" in result.output
