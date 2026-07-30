"""
Tests for the product:up command.

Pattern: filesystem via product_workspace fixture + mock subprocess.run
to avoid actually running docker compose.
"""

import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from splent_cli.commands.product.product_up import product_up


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _success_run(*args, **kwargs):
    """Always succeed subprocess.run mock."""
    return MagicMock(returncode=0, stdout="", stderr="")


# ---------------------------------------------------------------------------
# Flag validation
# ---------------------------------------------------------------------------


class TestFlagValidation:
    def test_rejects_both_dev_and_prod(self, runner, product_workspace):
        result = runner.invoke(product_up, ["--dev", "--prod"])
        assert result.exit_code == 1
        assert "both" in result.output.lower()

    def test_no_env_flag_and_no_splent_env(
        self, runner, product_workspace, monkeypatch
    ):
        monkeypatch.delenv("SPLENT_ENV", raising=False)
        result = runner.invoke(product_up, [])
        assert result.exit_code == 1
        assert "No environment specified" in result.output

    def test_requires_splent_app(self, runner, workspace):
        # Mock the docker daemon check so the test exercises the SPLENT_APP
        # guard regardless of whether docker is reachable in the environment.
        with patch("splent_cli.commands.product.product_up.require_docker"):
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 1
        assert "SPLENT_APP" in result.output


# ---------------------------------------------------------------------------
# Successful startup
# ---------------------------------------------------------------------------


class TestSuccessfulStartup:
    def test_starts_with_dev_flag(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 0
        assert "test_app" in result.output
        assert "✓" in result.output

    def test_starts_with_prod_flag(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_up, ["--prod"])
        assert result.exit_code == 0

    def test_starts_via_splent_env(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_up, [])  # SPLENT_ENV=dev set by fixture
        assert result.exit_code == 0

    def test_docker_compose_called_with_up_flag(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_up, ["--dev"])

        # Find calls that include "up"
        up_calls = [c for c in mock_run.call_args_list if "up" in c[0][0]]
        assert up_calls, "Expected at least one 'docker compose up' call"
        # Should use -d (detached)
        assert "-d" in up_calls[0][0][0]

    def test_passes_env_specific_compose_file(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_up, ["--dev"])

        args_used = [arg for call in mock_run.call_args_list for arg in call[0][0]]
        assert any("docker-compose.dev.yml" in a for a in args_used)


# ---------------------------------------------------------------------------
# Features startup
# ---------------------------------------------------------------------------


class TestFeaturesStartup:
    def test_no_features_still_starts_product(self, runner, product_workspace):
        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 0

    def test_features_started_before_product(self, runner, product_workspace, tmp_path):
        """Features must start first (dependency ordering)."""
        # Add a feature to pyproject.toml
        pyproject = tmp_path / "test_app" / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            "[project.optional-dependencies]\n"
            'features = ["splent_io/splent_feature_auth@v1.0.0"]\n'
        )
        # Create feature cache docker dir
        feat_docker = (
            tmp_path
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_auth@v1.0.0"
            / "docker"
        )
        feat_docker.mkdir(parents=True)
        (feat_docker / "docker-compose.dev.yml").write_text("services: {}")

        call_order = []

        def track_run(cmd, **kwargs):
            if "up" in cmd:
                call_order.append(cmd)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=track_run):
            result = runner.invoke(product_up, ["--dev"])

        assert result.exit_code == 0
        # Feature docker dir appears before product docker dir in calls
        if len(call_order) >= 2:
            first_project = call_order[0][call_order[0].index("-p") + 1]
            last_project = call_order[-1][call_order[-1].index("-p") + 1]
            assert "auth" in first_project  # feature first
            assert "test_app" in last_project  # product last


# ---------------------------------------------------------------------------
# A feature's stack belongs to the product that started it
# ---------------------------------------------------------------------------


def _declare_docker_feature(tmp_path):
    """Give test_app one pinned feature that contributes a Docker stack."""
    pyproject = tmp_path / "test_app" / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n'
        "[project.optional-dependencies]\n"
        'features = ["splent_io/splent_feature_auth@v1.0.0"]\n'
    )
    feat_docker = (
        tmp_path
        / ".splent_cache"
        / "features"
        / "splent_io"
        / "splent_feature_auth@v1.0.0"
        / "docker"
    )
    feat_docker.mkdir(parents=True)
    (feat_docker / "docker-compose.dev.yml").write_text("services: {}")
    return feat_docker


def _feature_up_call(mock_run):
    """The 'docker compose up' argv that started the feature's own stack."""
    for call in mock_run.call_args_list:
        cmd = call[0][0]
        if "up" in cmd and any(".splent_cache" in str(a) for a in cmd):
            return cmd
    raise AssertionError("no 'docker compose up' call for the feature stack")


class TestFeatureStackNaming:
    def test_feature_project_name_carries_the_product(
        self, runner, product_workspace, tmp_path
    ):
        """Two products pinning one feature version must not share a stack."""
        _declare_docker_feature(tmp_path)

        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            result = runner.invoke(product_up, ["--dev"])

        assert result.exit_code == 0
        cmd = _feature_up_call(mock_run)
        project = cmd[cmd.index("-p") + 1]
        assert project == "test_app_splent_io_splent_feature_auth_dev"

    def test_product_stack_keeps_its_own_name(
        self, runner, product_workspace, tmp_path
    ):
        _declare_docker_feature(tmp_path)

        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_up, ["--dev"])

        product_calls = [
            c[0][0]
            for c in mock_run.call_args_list
            if "up" in c[0][0] and not any(".splent_cache" in str(a) for a in c[0][0])
        ]
        assert product_calls
        cmd = product_calls[-1]
        assert cmd[cmd.index("-p") + 1] == "test_app_dev"

    def test_feature_stack_reads_the_product_env_file(
        self, runner, product_workspace, tmp_path
    ):
        """The offset host port only exists in the product's docker/.env."""
        _declare_docker_feature(tmp_path)
        product_env = tmp_path / "test_app" / "docker" / ".env"
        product_env.write_text("AUTH_HOST_PORT=9604\n")

        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_up, ["--dev"])

        cmd = _feature_up_call(mock_run)
        assert cmd[cmd.index("--env-file") + 1] == str(product_env)

    def test_env_file_omitted_when_the_product_has_none(
        self, runner, product_workspace, tmp_path
    ):
        """A product that never ran product:env still gets a usable command."""
        _declare_docker_feature(tmp_path)

        with patch("subprocess.run", side_effect=_success_run) as mock_run:
            runner.invoke(product_up, ["--dev"])

        assert "--env-file" not in _feature_up_call(mock_run)


# ---------------------------------------------------------------------------
# Missing pyproject.toml (lines 47-48)
# ---------------------------------------------------------------------------


class TestMissingPyproject:
    def test_exits_when_pyproject_missing(self, runner, tmp_path, monkeypatch):
        """Product docker dir exists but pyproject.toml is absent → exit 1."""
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.setenv("SPLENT_ENV", "dev")

        docker_dir = tmp_path / "test_app" / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / "docker-compose.dev.yml").write_text("services: {}")
        # Intentionally no pyproject.toml

        # Mock the docker daemon check so the test exercises the pyproject
        # guard regardless of whether docker is reachable in the environment.
        with patch("splent_cli.commands.product.product_up.require_docker"):
            result = runner.invoke(product_up, ["--dev"])
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output


# ---------------------------------------------------------------------------
# Missing compose file for a component
# ---------------------------------------------------------------------------


class TestMissingComposeFile:
    def test_warns_but_continues_on_missing_file(
        self, runner, product_workspace, tmp_path, monkeypatch
    ):
        """A missing docker-compose file shows a warning, doesn't crash."""
        # Add a feature with NO docker dir in cache
        pyproject = tmp_path / "test_app" / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n'
            "[project.optional-dependencies]\n"
            'features = ["splent_io/splent_feature_missing@v1.0.0"]\n'
        )

        with patch("subprocess.run", side_effect=_success_run):
            result = runner.invoke(product_up, ["--dev"])

        # Should still exit OK and launch the product itself
        assert result.exit_code == 0
