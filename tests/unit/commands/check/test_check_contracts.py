"""check:contracts — split dependency truth between contracts and UVL."""

from click.testing import CliRunner

from splent_cli.commands.check.check_contracts import check_contracts


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
    """Create a catalog with one SPL plus feature dirs with given requires."""
    spl = tmp_path / "splent_catalog" / "demo_spl"
    spl.mkdir(parents=True)
    (spl / "metadata.toml").write_text('[spl]\nname = "demo_spl"\n')
    (spl / "demo_spl.uvl").write_text(UVL)

    for short, requires in contracts.items():
        feat = tmp_path / f"splent_feature_{short}"
        feat.mkdir()
        deps = ", ".join(f'"{d}"' for d in requires)
        (feat / "pyproject.toml").write_text(
            "[tool.splent.contract.requires]\n" f"features = [{deps}]\n"
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
