"""Installing the declared features into a production image.

PyPI is the preferred source and the pinned git tag is the fallback. They are
the same artifact, since a release tags the commit and uploads the package
built from it, so the pin names one immutable thing either way. The fallback
exists because PyPI can refuse to create a new project and because a private
feature may deliberately never be published there. Without it, one
unpublished feature makes the whole product unbuildable.
"""

import pytest
from click.testing import CliRunner

from splent_cli.commands.feature import feature_pip_install as module
from splent_cli.commands.feature.feature_pip_install import (
    _parse_feature_entry,
    _pypi_does_not_have_it,
    feature_pip_install,
)


PYPI_MISSING = (
    "ERROR: Could not find a version that satisfies the requirement "
    "splent_feature_theme==0.2.1 (from versions: none)\n"
    "ERROR: No matching distribution found for splent_feature_theme==0.2.1"
)


class TestParsing:
    def test_it_keeps_both_spellings_of_the_version(self):
        """PyPI wants 1.2.7 and the tag is v1.2.7."""
        assert _parse_feature_entry("splent-io/splent_feature_auth@v1.2.7") == (
            "splent-io",
            "splent_feature_auth",
            "1.2.7",
            "v1.2.7",
        )

    def test_an_unpinned_entry_names_no_tag(self):
        assert _parse_feature_entry("splent-io/splent_feature_auth") == (
            "splent-io",
            "splent_feature_auth",
            None,
            None,
        )

    def test_an_entry_without_a_namespace_still_parses(self):
        assert _parse_feature_entry("splent_feature_auth@v1.2.7") == (
            "",
            "splent_feature_auth",
            "1.2.7",
            "v1.2.7",
        )


class TestFailureClassification:
    """Only 'PyPI has nothing' justifies reaching for the tag."""

    @pytest.mark.parametrize(
        "output",
        [
            PYPI_MISSING,
            "ERROR: 404 Client Error: Not Found for url: https://pypi.org/simple/x/",
        ],
    )
    def test_a_missing_package_is_worth_a_second_try(self, output):
        assert _pypi_does_not_have_it(output) is True

    @pytest.mark.parametrize(
        "output",
        [
            "ERROR: Could not install packages due to an OSError: [Errno 28] No space left",
            "WARNING: Retrying ... Temporary failure in name resolution",
            "ERROR: Failed building wheel for cryptography",
            "ERROR: ResolutionImpossible: dependency conflict",
        ],
    )
    def test_every_other_failure_is_reported_as_is(self, output):
        """From git these fail the same way, and the second error would bury
        the real one."""
        assert _pypi_does_not_have_it(output) is False


@pytest.fixture
def product(tmp_path, monkeypatch):
    """A product declaring two pinned features."""
    name = "wiki"
    (tmp_path / name).mkdir()
    (tmp_path / name / "pyproject.toml").write_text(
        "[tool.splent]\n"
        'features = ["splent_io/splent_feature_theme@v0.2.1", '
        '"splent_io/splent_feature_auth@v1.7.0"]\n'
    )
    monkeypatch.setattr(module.context, "require_app", lambda: name)
    monkeypatch.setattr(module.context, "workspace", lambda: tmp_path)
    return tmp_path / name


@pytest.fixture
def pip(monkeypatch):
    """Record every pip invocation and answer from a scripted table.

    The token is cleared because it changes the shape of the URL that gets
    built, and importing the CLI runs load_dotenv(), so whether the workspace
    token is present depends on which other tests ran first. A test about
    URLs has to decide that itself.
    """

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    calls = []
    answers = {}

    def fake_pip(spec):
        calls.append(spec)
        for prefix, reply in answers.items():
            if spec.startswith(prefix):
                return reply
        return True, ""

    monkeypatch.setattr(module, "_pip_install", fake_pip)
    return type("Pip", (), {"calls": calls, "answers": answers})()


def _run(args=None):
    return CliRunner().invoke(feature_pip_install, args or [])


class TestGitFallback:
    def test_pypi_is_tried_first_and_git_is_never_touched_when_it_works(
        self, product, pip
    ):
        result = _run()
        assert result.exit_code == 0
        assert pip.calls == [
            "splent_feature_theme==0.2.1",
            "splent_feature_auth==1.7.0",
        ]
        assert "git+" not in result.output

    def test_a_feature_pypi_does_not_serve_comes_from_its_tag(self, product, pip):
        pip.answers["splent_feature_theme=="] = (False, PYPI_MISSING)

        result = _run()

        assert result.exit_code == 0
        assert any("git+" in call and "@v0.2.1" in call for call in pip.calls)
        assert "installing its git tag v0.2.1" in result.output

    def test_the_summary_names_what_came_from_git(self, product, pip):
        """An image built from tags is reproducible, but you should know."""
        pip.answers["splent_feature_theme=="] = (False, PYPI_MISSING)

        result = _run()

        assert "splent_feature_theme v0.2.1" in result.output
        assert "PyPI and git tags" in result.output

    def test_the_hyphenated_org_is_tried_when_the_python_namespace_is_not_a_repo(
        self, product, pip
    ):
        """splent_io is the Python namespace; the repos live under splent-io."""
        pip.answers["splent_feature_theme=="] = (False, PYPI_MISSING)
        pip.answers["splent_feature_theme @ git+https://github.com/splent_io"] = (
            False,
            "fatal: repository not found",
        )

        result = _run()

        assert result.exit_code == 0
        assert any("github.com/splent-io" in call for call in pip.calls)

    def test_a_failure_that_is_not_about_a_missing_package_stops_there(
        self, product, pip
    ):
        pip.answers["splent_feature_theme=="] = (
            False,
            "ERROR: Failed building wheel for splent_feature_theme",
        )

        result = _run()

        assert result.exit_code == 1
        assert not any("git+" in call for call in pip.calls)
        assert "Failed building wheel" in result.output

    def test_pypi_only_refuses_the_fallback(self, product, pip):
        """A build policy may require that the image contains only published
        packages."""
        pip.answers["splent_feature_theme=="] = (False, PYPI_MISSING)

        result = _run(["--pypi-only"])

        assert result.exit_code == 1
        assert not any("git+" in call for call in pip.calls)

    def test_when_neither_channel_has_it_the_error_names_both(self, product, pip):
        pip.answers["splent_feature_theme=="] = (False, PYPI_MISSING)
        pip.answers["splent_feature_theme @ git+"] = (False, "fatal: not found")

        result = _run()

        assert result.exit_code == 1
        assert "not on PyPI" in result.output
        assert "could not be installed either" in result.output

    def test_an_unpinned_feature_has_no_tag_to_fall_back_to(
        self, tmp_path, monkeypatch, pip
    ):
        (tmp_path / "wiki").mkdir()
        (tmp_path / "wiki" / "pyproject.toml").write_text(
            '[tool.splent]\nfeatures = ["splent_io/splent_feature_theme"]\n'
        )
        monkeypatch.setattr(module.context, "require_app", lambda: "wiki")
        monkeypatch.setattr(module.context, "workspace", lambda: tmp_path)
        pip.answers["splent_feature_theme"] = (False, PYPI_MISSING)

        result = _run()

        assert result.exit_code == 1
        assert not any("git+" in call for call in pip.calls)


class TestTokenIsNeverEchoed:
    def test_the_token_does_not_reach_the_output(self, product, pip, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecret")
        pip.answers["splent_feature_theme=="] = (False, PYPI_MISSING)
        pip.answers["splent_feature_theme @ git+"] = (
            False,
            "fatal: could not read Username for 'https://ghp_supersecret@github.com'",
        )

        result = _run()

        assert "ghp_supersecret" not in result.output
        assert "***" in result.output
