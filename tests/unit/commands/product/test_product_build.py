"""
Tests for the product:build command.

product:build is pure filesystem: reads env files + docker-compose YAML,
merges them, and writes output files. No subprocess calls.
"""
import os
import yaml
import pytest
from click.testing import CliRunner

from splent_cli.commands.product.product_build import (
    product_build,
    load_env_file,
    merge_env_dicts,
    load_compose_file,
    merge_compose,
)


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------

class TestLoadEnvFile:
    def test_returns_empty_for_missing_file(self, tmp_path):
        assert load_env_file(str(tmp_path / "missing.env")) == {}

    def test_parses_key_value_pairs(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("FOO=bar\nBAZ=qux\n")
        assert load_env_file(str(f)) == {"FOO": "bar", "BAZ": "qux"}

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("# comment\n\nFOO=bar\n")
        assert load_env_file(str(f)) == {"FOO": "bar"}

    def test_ignores_lines_without_equals(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("NOEQUALS\nFOO=bar\n")
        assert load_env_file(str(f)) == {"FOO": "bar"}


class TestMergeEnvDicts:
    def test_override_wins(self):
        result = merge_env_dicts({"A": "1", "B": "2"}, {"B": "override", "C": "3"})
        assert result == {"A": "1", "B": "override", "C": "3"}

    def test_base_unchanged(self):
        base = {"A": "1"}
        merge_env_dicts(base, {"B": "2"})
        assert base == {"A": "1"}


class TestLoadComposeFile:
    def test_returns_empty_for_missing(self, tmp_path):
        assert load_compose_file(str(tmp_path / "missing.yml")) == {}

    def test_parses_yaml(self, tmp_path):
        f = tmp_path / "dc.yml"
        f.write_text("services:\n  web:\n    image: nginx\n")
        result = load_compose_file(str(f))
        assert result["services"]["web"]["image"] == "nginx"

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        f = tmp_path / "dc.yml"
        f.write_text("")
        assert load_compose_file(str(f)) == {}


class TestMergeCompose:
    def test_product_service_overrides_feature(self):
        base = {"services": {"web": {"image": "feature-web"}}}
        override = {"services": {"web": {"image": "product-web"}}}
        result = merge_compose(base, override)
        assert result["services"]["web"]["image"] == "product-web"

    def test_non_overlapping_services_merged(self):
        base = {"services": {"db": {"image": "postgres"}}}
        override = {"services": {"redis": {"image": "redis"}}}
        result = merge_compose(base, override)
        assert "db" in result["services"]
        assert "redis" in result["services"]

    def test_networks_merged(self):
        base = {"networks": {"net1": {}}}
        override = {"networks": {"net2": {}}}
        result = merge_compose(base, override)
        assert "net1" in result["networks"]
        assert "net2" in result["networks"]

    def test_volumes_merged(self):
        base = {"volumes": {"vol1": {}}}
        override = {"volumes": {"vol2": {}}}
        result = merge_compose(base, override)
        assert "vol1" in result["volumes"]
        assert "vol2" in result["volumes"]


# ---------------------------------------------------------------------------
# Integration: full CLI command
# ---------------------------------------------------------------------------

class TestProductBuildCommand:
    def test_exits_when_no_docker_dir(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        (tmp_path / "test_app").mkdir()
        result = runner.invoke(product_build, [])
        assert result.exit_code == 1
        assert "no docker/" in result.output.lower() or "docker" in result.output

    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(product_build, [])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output

    def test_creates_env_deploy_example(self, runner, product_workspace):
        docker_dir = product_workspace / "test_app" / "docker"
        (docker_dir / ".env.prod.example").write_text("API_KEY=secret\nDB_HOST=localhost\n")
        (docker_dir / "docker-compose.prod.yml").write_text("services: {}")

        result = runner.invoke(product_build, ["--skip-preflight"])
        assert result.exit_code == 0
        env_file = docker_dir / ".env.deploy.example"
        assert env_file.exists()
        content = env_file.read_text()
        assert "API_KEY=secret" in content

    def test_creates_docker_compose_deploy_yml(self, runner, product_workspace):
        docker_dir = product_workspace / "test_app" / "docker"
        compose_content = "services:\n  web:\n    image: nginx\n"
        (docker_dir / "docker-compose.prod.yml").write_text(compose_content)

        result = runner.invoke(product_build, ["--skip-preflight"])
        assert result.exit_code == 0
        deploy_file = docker_dir / "docker-compose.deploy.yml"
        assert deploy_file.exists()
        data = yaml.safe_load(deploy_file.read_text())
        assert "web" in data.get("services", {})

    def test_uses_env_example_fallback(self, runner, product_workspace):
        """Falls back to .env.example when .env.prod.example doesn't exist."""
        docker_dir = product_workspace / "test_app" / "docker"
        (docker_dir / ".env.example").write_text("MY_VAR=hello\n")
        (docker_dir / "docker-compose.prod.yml").write_text("services: {}")

        result = runner.invoke(product_build, ["--skip-preflight"])
        assert result.exit_code == 0
        content = (docker_dir / ".env.deploy.example").read_text()
        assert "MY_VAR=hello" in content

    def test_merges_feature_env(self, runner, product_workspace):
        """Feature env keys are included in the merged output."""
        docker_dir = product_workspace / "test_app" / "docker"
        (docker_dir / "docker-compose.prod.yml").write_text("services: {}")

        # Declare feature in pyproject.toml
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n\n'
            "[tool.splent]\n"
            'features = ["splent-io/splent_feature_auth"]\n'
        )

        # Create editable feature at workspace root
        feat_docker = product_workspace / "splent_feature_auth" / "docker"
        feat_docker.mkdir(parents=True)
        (feat_docker / ".env.example").write_text("AUTH_SECRET=abc\n")
        (feat_docker / "docker-compose.prod.yml").write_text("services:\n  auth:\n    image: auth\n")

        result = runner.invoke(product_build, ["--skip-preflight"], input="y\n")
        assert result.exit_code == 0
        content = (docker_dir / ".env.deploy.example").read_text()
        assert "AUTH_SECRET=abc" in content
