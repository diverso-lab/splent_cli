"""marketplace:index --local and the index consumers (search/info)."""

import json

from click.testing import CliRunner

from splent_cli.commands.marketplace.marketplace_index import marketplace_index
from splent_cli.commands.feature.feature_search import feature_search
from splent_cli.commands.feature.feature_info import feature_info
from splent_cli.services import marketplace


CONTRACT = """
[project]
name = "splent_feature_{short}"
version = "0.2.0"

[tool.splent.contract]
description = "{short} feature for tests"
archetype = "{archetype}"

[tool.splent.contract.provides]
routes = {routes}
services = {services}

[tool.splent.contract.requires]
features = {requires}
env_vars = []
signals = []
"""


def _workspace(tmp_path, monkeypatch):
    for short, archetype, routes, services, requires in (
        ("auth", "full", '["/login"]', '["AuthenticationService"]', "[]"),
        ("mail", "service", "[]", '["MailService"]', "[]"),
        ("contact", "full", '["/contact"]', '["ContactService"]', '["mail"]'),
    ):
        feat = tmp_path / f"splent_feature_{short}"
        feat.mkdir()
        (feat / "pyproject.toml").write_text(
            CONTRACT.format(
                short=short,
                archetype=archetype,
                routes=routes,
                services=services,
                requires=requires,
            )
        )
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.delenv("SPLENT_APP", raising=False)
    monkeypatch.delenv("SPLENT_INDEX_URL", raising=False)
    return tmp_path


class TestIndexLocal:
    def test_builds_index_from_workspace(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch)
        result = CliRunner().invoke(marketplace_index, ["--local"])
        assert result.exit_code == 0, result.output

        index_path = marketplace.index_cache_path(str(tmp_path))
        assert index_path.is_file()
        index = json.loads(index_path.read_text())
        shorts = [e["short"] for e in index["features"]]
        assert shorts == ["auth", "contact", "mail"]

        by_short = {e["short"]: e for e in index["features"]}
        assert by_short["mail"]["used_by"] == ["contact"]
        assert by_short["contact"]["requires"]["features"] == ["mail"]
        assert by_short["auth"]["archetype"] == "full"

    def test_custom_output_path(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch)
        out = tmp_path / "public" / "index.json"
        result = CliRunner().invoke(
            marketplace_index, ["--local", "--output", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert out.is_file()


class TestSearchOverIndex:
    def _build(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch)
        result = CliRunner().invoke(marketplace_index, ["--local"])
        assert result.exit_code == 0, result.output

    def test_search_by_query(self, tmp_path, monkeypatch):
        self._build(tmp_path, monkeypatch)
        result = CliRunner().invoke(feature_search, ["contact"])
        assert result.exit_code == 0, result.output
        assert "contact" in result.output
        assert "mail" not in result.output.replace("contact", "")

    def test_search_filter_archetype(self, tmp_path, monkeypatch):
        self._build(tmp_path, monkeypatch)
        result = CliRunner().invoke(feature_search, ["--archetype", "service"])
        assert result.exit_code == 0, result.output
        assert "mail" in result.output
        assert "auth" not in result.output

    def test_search_filter_provides(self, tmp_path, monkeypatch):
        self._build(tmp_path, monkeypatch)
        result = CliRunner().invoke(feature_search, ["--provides", "MailService"])
        assert result.exit_code == 0, result.output
        assert "mail" in result.output

    def test_search_filter_requires(self, tmp_path, monkeypatch):
        self._build(tmp_path, monkeypatch)
        result = CliRunner().invoke(feature_search, ["--requires", "mail"])
        assert result.exit_code == 0, result.output
        assert "contact" in result.output


class TestInfoOverIndex:
    def test_info_shows_contract_card(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch)
        CliRunner().invoke(marketplace_index, ["--local"])
        result = CliRunner().invoke(feature_info, ["contact"])
        assert result.exit_code == 0, result.output
        assert "contact feature for tests" in result.output
        assert "mail" in result.output  # requires
        assert "/contact" in result.output

    def test_info_json(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch)
        CliRunner().invoke(marketplace_index, ["--local"])
        result = CliRunner().invoke(feature_info, ["mail", "--json"])
        assert result.exit_code == 0, result.output
        entry = json.loads(result.output)
        assert entry["short"] == "mail"
        assert entry["used_by"] == ["contact"]

    def test_info_unknown_feature_fails_cleanly(self, tmp_path, monkeypatch):
        _workspace(tmp_path, monkeypatch)
        CliRunner().invoke(marketplace_index, ["--local"])
        monkeypatch.setattr(
            "splent_cli.services.registry.latest_semver_tag",
            lambda *a, **k: None,
        )
        result = CliRunner().invoke(feature_info, ["ghost"])
        assert result.exit_code == 1
        assert "not found" in result.output

    def test_info_live_fallback_without_index(self, tmp_path, monkeypatch):
        # No index anywhere: feature:info reads the pyproject straight from
        # GitHub at the latest released tag.
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("SPLENT_APP", raising=False)
        monkeypatch.delenv("SPLENT_INDEX_URL", raising=False)
        monkeypatch.setattr(
            "splent_cli.services.registry.latest_semver_tag",
            lambda org, repo, token=None: "v0.2.0",
        )
        monkeypatch.setattr(
            "splent_cli.services.registry.fetch_file",
            lambda org, repo, path, ref=None, token=None: CONTRACT.format(
                short="mail",
                archetype="service",
                routes="[]",
                services='["MailService"]',
                requires="[]",
            ),
        )
        result = CliRunner().invoke(feature_info, ["mail"])
        assert result.exit_code == 0, result.output
        assert "mail feature for tests" in result.output
        assert "v0.2.0" in result.output
        assert "github (live)" in result.output


class TestRegistryFile:
    def test_registry_file_sources_merge_and_dedupe(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        registry_toml = tmp_path / "registry.toml"
        registry_toml.write_text(
            "[registry]\n"
            'orgs = ["third-org", "splent-io"]\n'
            'repos = ["other/splent_feature_x", "splent_feature_sin_org", 123]\n'
        )

        captured = {}

        def _fake_build(orgs, repos, **kwargs):
            captured["orgs"] = orgs
            captured["repos"] = repos
            return [], []

        monkeypatch.setattr(
            "splent_cli.services.marketplace.build_remote_features", _fake_build
        )
        result = CliRunner().invoke(
            marketplace_index,
            [
                "--org",
                "splent-io",
                "--repo",
                "other/splent_feature_x",
                "--registry",
                str(registry_toml),
            ],
        )
        assert result.exit_code == 0, result.output
        # Dedupe against explicit flags; invalid entries silently dropped.
        assert captured["orgs"] == ["splent-io", "third-org"]
        assert captured["repos"] == ["other/splent_feature_x"]

    def test_registry_file_missing_is_clean_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        result = CliRunner().invoke(
            marketplace_index, ["--registry", str(tmp_path / "nope.toml")]
        )
        assert result.exit_code != 0
        assert "registry file" in result.output
        assert "Traceback" not in result.output

    def test_rate_limit_aborts_without_writing_index(self, tmp_path, monkeypatch):
        from splent_cli.services import registry as reg

        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        def _limited(*a, **k):
            raise reg.RegistryError(
                "GitHub API error (HTTP 403)", status=403, rate_limited=True
            )

        monkeypatch.setattr("splent_cli.services.registry.list_org_repos", _limited)
        result = CliRunner().invoke(marketplace_index, [])
        assert result.exit_code == 1
        assert "rate limit" in result.output.lower()
        assert "GITHUB_TOKEN" in result.output
        assert "Traceback" not in result.output
        assert not marketplace.index_cache_path(str(tmp_path)).is_file()
