"""
Tests for the product:list command.
"""
import tomli_w
import pytest
from click.testing import CliRunner

from splent_cli.commands.product.product_list import product_list, _product_info


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_product(workspace, name, version="1.0.0", features=None):
    d = workspace / name
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "project": {
            "name": name,
            "version": version,
            "optional-dependencies": {"features": features or []},
        }
    }
    with open(d / "pyproject.toml", "wb") as f:
        tomli_w.dump(data, f)
    return d


# ---------------------------------------------------------------------------
# _product_info() helper
# ---------------------------------------------------------------------------

class TestProductInfo:
    def test_returns_none_for_missing_pyproject(self, tmp_path):
        d = tmp_path / "app"
        d.mkdir()
        assert _product_info(d) is None

    def test_returns_name_version_features(self, tmp_path):
        d = _make_product(tmp_path, "myapp", "2.1.0", ["ns/auth@v1"])
        info = _product_info(d)
        assert info["name"] == "myapp"
        assert info["version"] == "2.1.0"
        assert info["features"] == 1

    def test_returns_zero_features_when_empty(self, tmp_path):
        d = _make_product(tmp_path, "myapp")
        info = _product_info(d)
        assert info["features"] == 0

    def test_returns_none_for_corrupt_toml(self, tmp_path):
        d = tmp_path / "app"
        d.mkdir()
        (d / "pyproject.toml").write_text("NOT VALID TOML {{{{")
        assert _product_info(d) is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestProductListCommand:
    def test_no_products_shows_info(self, runner, workspace):
        result = runner.invoke(product_list, [])
        assert result.exit_code == 0
        assert "No products" in result.output

    def test_shows_product_name(self, runner, workspace):
        _make_product(workspace, "test_app")
        result = runner.invoke(product_list, [])
        assert result.exit_code == 0
        assert "test_app" in result.output

    def test_shows_version(self, runner, workspace):
        _make_product(workspace, "test_app", version="3.2.1")
        result = runner.invoke(product_list, [])
        assert "3.2.1" in result.output

    def test_shows_feature_count(self, runner, workspace):
        _make_product(workspace, "test_app", features=["ns/auth@v1", "ns/pay@v2"])
        result = runner.invoke(product_list, [])
        assert "2 features" in result.output

    def test_singular_feature_label(self, runner, workspace):
        _make_product(workspace, "test_app", features=["ns/auth@v1"])
        result = runner.invoke(product_list, [])
        assert "1 feature" in result.output
        assert "features" not in result.output.split("1 feature")[1][:5]

    def test_active_product_marked(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        _make_product(workspace, "test_app")
        result = runner.invoke(product_list, [])
        assert "active" in result.output.lower()

    def test_inactive_product_not_marked(self, runner, workspace, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "other_app")
        _make_product(workspace, "test_app")
        result = runner.invoke(product_list, [])
        assert "◀ active" not in result.output

    def test_ignores_hidden_dirs(self, runner, workspace):
        _make_product(workspace, ".splent_cache")
        result = runner.invoke(product_list, [])
        assert "No products" in result.output

    def test_ignores_dirs_without_pyproject(self, runner, workspace):
        (workspace / "bare_dir").mkdir()
        result = runner.invoke(product_list, [])
        assert "No products" in result.output

    def test_multiple_products_listed(self, runner, workspace):
        _make_product(workspace, "app1")
        _make_product(workspace, "app2")
        result = runner.invoke(product_list, [])
        assert "app1" in result.output
        assert "app2" in result.output
