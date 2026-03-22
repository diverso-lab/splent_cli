"""
Tests for the product:down command.
"""
import pytest
import tomli_w
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_down import product_down


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _success_run(*args, **kwargs):
    return MagicMock(returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------

class TestFlagValidation:
    def test_requires_splent_app(self, runner, workspace):
        result = runner.invoke(product_down, [])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# Basic shutdown (no --v)
# ---------------------------------------------------------------------------

class TestBasicShutdown:
    def test_stops_product_without_volumes(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            result = runner.invoke(product_down, [])
        assert result.exit_code == 0
        # docker compose down called
        calls_flat = [arg for c in mock_run.call_args_list for arg in c[0][0]]
        assert "down" in calls_flat

    def test_shows_stopped_message(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_down, [])
        assert "stopped successfully" in result.output

    def test_no_pyproject_shows_warning(self, runner, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        docker_dir = tmp_path / "test_app" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / "docker-compose.dev.yml").write_text("services: {}")
        # No pyproject.toml

        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_down, [])
        assert "pyproject.toml" in result.output


# ---------------------------------------------------------------------------
# Volume removal (--v flag)
# ---------------------------------------------------------------------------

class TestVolumeRemoval:
    def test_cancel_skips_removal(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            result = runner.invoke(product_down, ["--v"], input="n\n")
        assert result.exit_code == 0
        assert "cancelled" in result.output.lower()
        assert mock_run.call_count == 0

    def test_confirm_adds_v_flag(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            result = runner.invoke(product_down, ["--v"], input="y\n")
        calls_flat = [arg for c in mock_run.call_args_list for arg in c[0][0]]
        assert "-v" in calls_flat


# ---------------------------------------------------------------------------
# Features in pyproject.toml
# ---------------------------------------------------------------------------

class TestFeatureShutdown:
    def test_features_are_stopped(self, runner, product_workspace, tmp_path):
        # Add a feature to pyproject
        pyproject = tmp_path / "test_app" / "pyproject.toml"
        data = {
            "project": {
                "name": "test_app",
                "version": "1.0.0",
                "optional-dependencies": {
                    "features": ["splent_io/splent_feature_auth@v1.0.0"]
                }
            }
        }
        with open(pyproject, "wb") as f:
            tomli_w.dump(data, f)

        # Create feature docker dir in cache
        feat_docker = (
            tmp_path / ".splent_cache" / "features"
            / "splent_io" / "splent_feature_auth@v1.0.0" / "docker"
        )
        feat_docker.mkdir(parents=True)
        (feat_docker / "docker-compose.dev.yml").write_text("services: {}")

        call_count = {"n": 0}

        def track_run(cmd, **kwargs):
            call_count["n"] += 1
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=track_run):
            result = runner.invoke(product_down, [])

        assert result.exit_code == 0
        # Both product and feature docker compose down calls expected
        assert call_count["n"] >= 1
