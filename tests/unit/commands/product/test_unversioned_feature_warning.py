"""What product:build says about features declared without a version.

The warning used to read: "Production builds install features from PyPI.
Editable features have no published version and will fail during the Docker
build." Half of that is wrong in a way that costs hours. The build does not
fail. `splent feature:pip-install`, which the production Dockerfile runs,
turns a versionless entry into a bare `pip install <name>`, so pip installs
whatever PyPI serves under that name. The image builds and ships code that is
not the code in the workspace.

The other half of the confusion is the reverse: `pip install -e` against the
local checkout is a DEVELOPMENT-only path. It lives in
scripts/00_install_features.sh, which only entrypoint.dev.sh runs, and
product:resolve is what fills those paths, by cloning from GitHub at the tag.

These tests hold both halves in place, in the messages and in the templates
that generate the two scripts, so neither drifts back.
"""

from pathlib import Path

import pytest
from click.testing import CliRunner

from splent_cli.commands.product.product_build import product_build
from splent_cli.commands.feature.feature_pip_install import (
    _uninstalled_env_features,
)


TEMPLATES = (
    Path(__file__).resolve().parents[4] / "src" / "splent_cli" / "templates" / "product"
)


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _product_with_unversioned_feature(workspace):
    docker_dir = workspace / "test_app" / "docker"
    (docker_dir / "docker-compose.prod.yml").write_text("services: {}")
    (workspace / "test_app" / "pyproject.toml").write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n\n'
        "[tool.splent]\n"
        'features = ["splent-io/splent_feature_notes"]\n'
    )


class TestTheWarningDescribesTheCode:
    def test_it_no_longer_claims_the_docker_build_will_fail(
        self, runner, product_workspace
    ):
        _product_with_unversioned_feature(product_workspace)

        result = runner.invoke(product_build, ["--skip-preflight"], input="n\n")

        out = result.output + (result.stderr or "")
        assert "will fail during the Docker build" not in out
        assert "will fail the Docker build" not in out

    def test_it_says_what_pip_actually_installs(self, runner, product_workspace):
        _product_with_unversioned_feature(product_workspace)

        result = runner.invoke(product_build, ["--skip-preflight"], input="n\n")

        out = result.output + (result.stderr or "")
        assert "bare package name" in out
        assert "NOT your local checkout" in out

    def test_it_points_at_the_script_that_does_use_the_local_checkout(
        self, runner, product_workspace
    ):
        """The dev path is where pip install -e happens, and it says so."""
        _product_with_unversioned_feature(product_workspace)

        result = runner.invoke(product_build, ["--skip-preflight"], input="n\n")

        out = result.output + (result.stderr or "")
        assert "00_install_features.sh" in out
        assert "pip install -e" in out
        assert "product:resolve" in out

    def test_declining_still_aborts(self, runner, product_workspace):
        _product_with_unversioned_feature(product_workspace)

        result = runner.invoke(product_build, ["--skip-preflight"], input="n\n")

        assert result.exit_code == 1

    def test_a_fully_versioned_product_says_nothing_about_any_of_this(
        self, runner, product_workspace
    ):
        docker_dir = product_workspace / "test_app" / "docker"
        (docker_dir / "docker-compose.prod.yml").write_text("services: {}")
        (product_workspace / "test_app" / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n\n'
            "[tool.splent]\n"
            'features = ["splent-io/splent_feature_notes@v1.0.0"]\n'
        )

        result = runner.invoke(product_build, ["--skip-preflight"])

        assert result.exit_code == 0
        assert "bare package name" not in result.output


class TestTheTemplatesAgreeWithTheMessages:
    def test_the_install_script_says_it_is_the_dev_path_and_names_its_caller(self):
        text = (TEMPLATES / "product_00_install_features.sh.j2").read_text()
        assert "never contacts PyPI" in text
        assert "entrypoint.dev.sh" in text
        assert "product:resolve" in text

    def test_the_install_script_really_only_installs_from_a_local_path(self):
        """The claim in the header has to match the only pip call in the file."""
        text = (TEMPLATES / "product_00_install_features.sh.j2").read_text()
        pip_calls = [
            line.strip()
            for line in text.splitlines()
            if "pip install" in line and not line.strip().startswith("#")
        ]
        assert pip_calls, "no pip install in the install script"
        for call in pip_calls:
            assert " -e " in call, call

    def test_the_prod_dockerfile_says_it_is_the_only_install_in_the_image(self):
        text = (TEMPLATES / "product_Dockerfile.prod.j2").read_text()
        assert "splent feature:pip-install" in text
        assert "00_install_features.sh" in text
        assert "never runs here" in text

    def test_the_dev_entrypoint_is_the_only_caller_of_the_install_script(self):
        dev = (TEMPLATES / "product_entrypoint.dev.sh.j2").read_text()
        prod = (TEMPLATES / "product_entrypoint.prod.sh.j2").read_text()
        assert "00_install_features.sh" in dev
        assert "00_install_features.sh" not in prod


class TestFeaturePipInstallReportsWhatItSkips:
    def test_env_only_features_are_named(self):
        skipped = _uninstalled_env_features(
            {
                "features": ["splent-io/splent_feature_auth@v1.0.0"],
                "features_prod": ["splent-io/splent_feature_cloudflare@v0.1.0"],
                "features_dev": ["splent-io/splent_feature_tools"],
            }
        )
        assert "splent-io/splent_feature_cloudflare@v0.1.0  [features_prod]" in skipped
        assert "splent-io/splent_feature_tools  [features_dev]" in skipped

    def test_a_feature_in_both_lists_is_not_reported(self):
        entry = "splent-io/splent_feature_auth@v1.0.0"
        assert (
            _uninstalled_env_features({"features": [entry], "features_prod": [entry]})
            == []
        )

    def test_nothing_declared_elsewhere_reports_nothing(self):
        assert _uninstalled_env_features({"features": ["a"]}) == []
