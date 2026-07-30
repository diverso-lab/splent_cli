"""check:contracts — split dependency truth between contracts and UVL."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from splent_cli.commands.check.check_contracts import (
    _exists_remotely,
    check_contracts,
)
from tests.conftest import make_spl_working_copy


@pytest.fixture(autouse=True)
def features_exist_remotely():
    """Every feature in these fixtures is assumed to exist on GitHub.

    check:contracts asks GitHub about features it cannot find locally, to tell
    "not cloned yet" from "declared but never written". That question belongs
    to the one test that is about it; everywhere else it would put a real
    network call in a unit test and make the outcome depend on which of these
    fixture names happens to be a released repository.
    """
    with patch(
        "splent_cli.commands.check.check_contracts._exists_remotely",
        return_value=True,
    ):
        yield


UVL = """features
\tdemo_spl
\t\tmandatory
\t\t\tauth {org 'splent-io', package 'splent_feature_auth'}
\t\toptional
\t\t\tteam {org 'splent-io', package 'splent_feature_team'}
\t\t\tmedia {org 'splent-io', package 'splent_feature_media'}
\t\t\tnotes {org 'splent-io', package 'splent_feature_notes'}
constraints
\tteam => media
"""


def _workspace(tmp_path, monkeypatch, contracts: dict[str, list[str]]):
    """Create a working copy of one SPL plus feature dirs with given requires."""
    make_spl_working_copy(tmp_path, "demo_spl", UVL)

    for short, requires in contracts.items():
        feat = tmp_path / f"splent_feature_{short}"
        feat.mkdir()
        deps = ", ".join(f'"{d}"' for d in requires)
        (feat / "pyproject.toml").write_text(
            f"[tool.splent.contract.requires]\nfeatures = [{deps}]\n"
        )

    monkeypatch.setenv("WORKING_DIR", str(tmp_path))


class TestCheckContracts:
    def test_agreement_passes(self, tmp_path, monkeypatch):
        _workspace(
            tmp_path,
            monkeypatch,
            {"auth": [], "team": ["media"], "media": [], "notes": []},
        )
        result = CliRunner().invoke(check_contracts, [])
        assert result.exit_code == 0
        assert "agree" in result.output

    def test_contract_dep_without_uvl_constraint_is_error(self, tmp_path, monkeypatch):
        # notes requires auth in its contract, but the UVL has no notes => auth.
        _workspace(
            tmp_path,
            monkeypatch,
            {"auth": [], "team": ["media"], "media": [], "notes": ["auth"]},
        )
        result = CliRunner().invoke(check_contracts, [])
        assert result.exit_code == 1
        assert "notes" in result.output
        assert "no 'notes => auth' constraint" in result.output
        assert "spl:add-constraints" in result.output

    def test_uvl_constraint_without_contract_is_warning(self, tmp_path, monkeypatch):
        # UVL says team => media but team's contract declares nothing.
        _workspace(
            tmp_path,
            monkeypatch,
            {"auth": [], "team": [], "media": [], "notes": []},
        )
        result = CliRunner().invoke(check_contracts, [])
        assert result.exit_code == 0  # warning, not error
        assert "team => media" in result.output
        assert "does not" in result.output

    def test_missing_local_features_are_skipped(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch, {"team": ["media"], "media": []})
        result = CliRunner().invoke(check_contracts, [])
        assert result.exit_code == 0
        assert "not local" in result.output
        assert "auth" in result.output


class TestAFeatureTheModelOffersButNobodyWrote:
    """A variability model is a promise about which products can be built.

    A feature that exists nowhere is offered by product:configure all the
    same, selected, written into the pyproject, and only fails at derive time
    on a repository that returns 404. That used to read as "not local", which
    is the right words for a feature that is merely not cloned.
    """

    def test_it_is_an_error_and_not_an_aside(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch, {"team": ["media"], "media": [], "notes": []})

        with patch(
            "splent_cli.commands.check.check_contracts._exists_remotely",
            return_value=False,
        ):
            result = CliRunner().invoke(check_contracts, [])

        assert result.exit_code == 1
        assert "does not exist" in result.output
        assert "splent-io/splent_feature_auth" in result.output

    def test_a_feature_merely_not_cloned_stays_an_aside(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch, {"team": ["media"], "media": [], "notes": []})
        result = CliRunner().invoke(check_contracts, [])

        assert result.exit_code == 0
        assert "not local" in result.output
        assert "does not exist" not in result.output

    def test_the_network_being_unreachable_never_invents_a_missing_feature(self):
        """Everything else here is checked from disk; a diagnostic that failed
        the build because the wifi dropped would be worse than a quiet one."""
        with patch("urllib.request.urlopen", side_effect=OSError("no network")):
            assert _exists_remotely("splent-io", "whatever") is True

    def test_a_feature_with_no_org_is_left_alone(self):
        """Nothing to look under, so nothing can be concluded."""
        assert _exists_remotely(None, "splent_feature_x") is True
