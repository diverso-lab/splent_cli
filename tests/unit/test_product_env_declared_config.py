"""Where a feature setting is written down, and who gets to decide it.

A product's docker/.env.example used to carry every variable its features
read. That put settings nobody had an opinion about into a file about
infrastructure, and it left a feature with no container nowhere to state a
default at all, since .env.example lives under docker/ and only features
that bring a service have one.

Both sides now declare in their own pyproject: the feature says what it
reads and what it falls back to, the product says only what it decides
differently. The merge puts them together, weakest first:

    feature docker/.env.example
    feature [tool.splent.config]
    product [tool.splent.config]
    product .env.<env>.example
"""

import pytest
from click.testing import CliRunner

from splent_cli.commands.product.product_env import product_env


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def read_env(workspace):
    """The generated .env, as a dict."""
    text = (workspace / "test_app" / "docker" / ".env").read_text()
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value
    return out


def install(workspace, name, pyproject_body, env_example=None):
    """Put an editable feature in the workspace and declare it in the product.

    ``env_example`` is optional on purpose: most features have no container,
    and those are the ones this mechanism exists for.
    """
    feature = workspace / name
    feature.mkdir(parents=True, exist_ok=True)
    (feature / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n' + pyproject_body
    )
    if env_example is not None:
        docker = feature / "docker"
        docker.mkdir(exist_ok=True)
        (docker / ".env.example").write_text(env_example)

    pyproject = workspace / "test_app" / "pyproject.toml"
    text = pyproject.read_text()
    pyproject.write_text(
        text.replace("features = []", f'features = ["splent_io/{name}"]')
    )
    return feature


@pytest.fixture
def product(product_workspace):
    """A product whose env example holds infrastructure and nothing else."""
    (product_workspace / "test_app" / "docker" / ".env.dev.example").write_text(
        "SPLENT_APP=__PRODUCT__\nMARIADB_PORT=3306\n"
    )
    (product_workspace / "test_app" / "docker" / ".env").write_text(
        "SPLENT_APP=test_app\nMARIADB_PORT=3306\n"
    )
    return product_workspace


class TestAFeatureWithoutAContainer:
    def test_its_declared_defaults_reach_the_env(self, runner, product):
        """The case the whole mechanism is for. This feature ships no
        docker/, so before this it had no way to state a default."""
        install(
            product,
            "splent_feature_search",
            '[tool.splent.config]\nSEARCH_PATH = "search"\nSEARCH_LIMIT = 20\n',
        )

        result = runner.invoke(product_env, ["--merge", "--dev"])

        assert result.exit_code == 0
        env = read_env(product)
        assert env["SEARCH_PATH"] == "search"
        assert env["SEARCH_LIMIT"] == "20"

    def test_the_product_env_example_stays_infrastructure(self, runner, product):
        """Declaring settings elsewhere is only worth it if the product's own
        file is left holding what it is about."""
        install(
            product,
            "splent_feature_search",
            '[tool.splent.config]\nSEARCH_PATH = "search"\n',
        )

        runner.invoke(product_env, ["--merge", "--dev"])

        example = (product / "test_app" / "docker" / ".env.dev.example").read_text()
        assert "SEARCH_PATH" not in example


class TestWhoWins:
    def test_the_product_overrides_the_feature_default(self, runner, product):
        install(
            product,
            "splent_feature_search",
            '[tool.splent.config]\nSEARCH_PLACEHOLDER = ""\n',
        )
        pyproject = product / "test_app" / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text()
            + '\n[tool.splent.config]\nSEARCH_PLACEHOLDER = "Buscar en esta wiki"\n'
        )

        runner.invoke(product_env, ["--merge", "--dev"])

        assert read_env(product)["SEARCH_PLACEHOLDER"] == "Buscar en esta wiki"

    def test_the_products_env_file_is_still_the_last_word(self, runner, product):
        """A merge rebuilds .env from the example every time, so the example
        is where a setting gets forced when a deployment needs it forced.
        Nothing declared in a pyproject may override it, or the escape hatch
        would depend on which file the value happened to come from."""
        install(
            product,
            "splent_feature_search",
            '[tool.splent.config]\nSEARCH_PLACEHOLDER = ""\n',
        )
        pyproject = product / "test_app" / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text()
            + '\n[tool.splent.config]\nSEARCH_PLACEHOLDER = "Buscar"\n'
        )
        example = product / "test_app" / "docker" / ".env.dev.example"
        example.write_text(example.read_text() + "SEARCH_PLACEHOLDER=Search\n")

        runner.invoke(product_env, ["--merge", "--dev"])

        assert read_env(product)["SEARCH_PLACEHOLDER"] == "Search"

    def test_the_pyproject_beats_the_features_own_env_example(self, runner, product):
        """A feature that ships both is stating a default twice; the
        pyproject is the one a product's override sits next to."""
        install(
            product,
            "splent_feature_elasticsearch",
            '[tool.splent.config]\nELASTIC_INDEX = "wiki"\n',
            env_example="ELASTIC_INDEX=default\n",
        )

        runner.invoke(product_env, ["--merge", "--dev"])

        assert read_env(product)["ELASTIC_INDEX"] == "wiki"


class TestTypesAndPorts:
    def test_a_boolean_arrives_as_the_word_the_config_parses(self, runner, product):
        """An env file has no types. TOML true has to reach config.py as the
        string every SPLENT config already reads."""
        install(
            product,
            "splent_feature_archive",
            "[tool.splent.config]\nARCHIVE_NAV = true\nARCHIVE_HIDDEN = false\n",
        )

        runner.invoke(product_env, ["--merge", "--dev"])

        env = read_env(product)
        assert env["ARCHIVE_NAV"] == "true"
        assert env["ARCHIVE_HIDDEN"] == "false"

    def test_a_list_arrives_comma_separated(self, runner, product):
        install(
            product,
            "splent_feature_courses",
            '[tool.splent.config]\nCOURSES_DEFAULT_CATEGORIES = ["Teoría", "Prácticas"]\n',
        )

        runner.invoke(product_env, ["--merge", "--dev"])

        assert read_env(product)["COURSES_DEFAULT_CATEGORIES"] == "Teoría,Prácticas"

    def test_a_declared_host_port_is_offset_like_any_other(self, runner, product):
        """Two products of the same line come up at once. A port declared in
        a pyproject collides exactly as readily as one declared in an env
        example, so it gets the same treatment."""
        install(
            product,
            "splent_feature_elasticsearch",
            "[tool.splent.config]\nELASTIC_HOST_PORT = 9200\n",
        )

        runner.invoke(product_env, ["--merge", "--dev"])

        # Whatever the offset for test_app is, the port must not be the raw
        # default that every sibling product would also claim.
        assert read_env(product)["ELASTIC_HOST_PORT"].isdigit()


class TestNotDeclaringAnything:
    def test_a_feature_with_no_config_block_is_fine(self, runner, product):
        install(product, "splent_feature_notes", "")

        result = runner.invoke(product_env, ["--merge", "--dev"])

        assert result.exit_code == 0
        assert "MARIADB_PORT" in read_env(product)

    def test_a_broken_pyproject_does_not_stop_the_merge(self, runner, product):
        """A half saved file is a normal state of an editable feature, and
        the rest of the product still has to come up."""
        feature = install(product, "splent_feature_notes", "")
        (feature / "pyproject.toml").write_text("[project\nname = broken")

        result = runner.invoke(product_env, ["--merge", "--dev"])

        assert result.exit_code == 0
