"""Settings a feature reads but never writes down.

A variable that exists only as an ``os.getenv`` fallback is invisible: a
product cannot discover the knob, and the pyproject says nothing about what
happens if it is left alone. feature:contract already knows which variables
the source reads, so it is the right place to notice.
"""

import pytest

from splent_cli.commands.feature.feature_contract import _check_declared_config


@pytest.fixture
def feature(tmp_path):
    def build(pyproject_body="", env_example=None):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "splent_feature_probe"\n' + pyproject_body
        )
        if env_example is not None:
            docker = tmp_path / "docker"
            docker.mkdir()
            (docker / ".env.example").write_text(env_example)
        return tmp_path

    return build


@pytest.fixture
def report(capsys):
    """What the check prints for a given set of variables."""

    def run(path, env_vars):
        _check_declared_config(path, {"env_vars": env_vars})
        return capsys.readouterr().out

    return run


class TestWhatIsReported:
    def test_an_undeclared_setting_is_named(self, feature, report):
        assert "POST_PERMALINK" in report(feature(), ["POST_PERMALINK"])

    def test_a_declared_setting_is_not(self, feature, report):
        path = feature('[tool.splent.config]\nPOST_PERMALINK = "/%postname%"\n')

        assert "POST_PERMALINK" not in report(path, ["POST_PERMALINK"])

    def test_a_setting_in_the_features_env_example_counts_as_declared(
        self, feature, report
    ):
        """A feature that ships a service may state a default next to its
        compose file, which product:env --merge reads just the same."""
        path = feature(env_example="ELASTICSEARCH_URL=http://es:9200\n")

        assert "ELASTICSEARCH_URL" not in report(path, ["ELASTICSEARCH_URL"])


class TestWhatIsNotAFeaturesToDeclare:
    @pytest.mark.parametrize(
        "name",
        [
            "MAIL_PASSWORD",
            "RECAPTCHA_SECRET_KEY",
            "UVLHUB_API_KEY",
            "TURNSTILE_SITE_KEY",
        ],
    )
    def test_a_credential_is_never_asked_for(self, feature, report, name):
        """Its default has no business in a committed file, so its absence
        from the declaration is correct and must not be nagged about."""
        assert name not in report(feature(), [name])

    @pytest.mark.parametrize("name", ["WORKING_DIR", "SPLENT_APP", "SPLENT_NETWORK"])
    def test_a_framework_variable_is_not_the_features(self, feature, report, name):
        """Reading one is asking where it is running, not offering a knob."""
        assert name not in report(feature(), [name])


class TestNotGettingInTheWay:
    def test_a_feature_that_reads_nothing_says_nothing(self, feature, report):
        assert report(feature(), []) == ""

    def test_a_broken_pyproject_is_not_an_error(self, feature, report):
        """Half a saved file is a normal state of an editable feature."""
        path = feature()
        (path / "pyproject.toml").write_text("[project\nname = broken")

        assert report(path, ["POST_PERMALINK"]) == ""
