"""Unit tests for services/marketplace.py — index building and querying."""

import json
from pathlib import Path

from splent_cli.services import marketplace


CONTRACT_TOML = """
[project]
name = "splent_feature_events"
version = "0.3.0"

[tool.splent]
cli_version = "1.13.0"

[tool.splent.contract]
description = "Events for the public site"
archetype = "full"
category = "content"
tags = ["events", "cms"]

[tool.splent.contract.provides]
routes = ["/events", "/events/<slug>"]
models = ["Event"]
services = ["EventsService"]
hooks = ["layout.authenticated_sidebar"]

[tool.splent.contract.requires]
features = ["auth"]
env_vars = []
signals = []
"""


UVL_TEXT = """features
\tcms_spl
\t\tmandatory
\t\t\ttheme {org 'splent-io', package 'splent_feature_theme'}
\t\t\tsession
\t\t\t\talternative
\t\t\t\t\tsession_filesystem {org 'splent-io', package 'splent_feature_session_filesystem'}
\t\t\t\t\tsession_redis {org 'splent-io', package 'splent_feature_session_redis'}
\t\toptional
\t\t\tevents {org 'splent-io', package 'splent_feature_events'}
\t\t\tmedia {org 'splent-io', package 'splent_feature_media'}
constraints
\tevents => media
\tsession_redis => redis
"""


def _entry(short, requires=(), provides_routes=(), org="splent-io"):
    return {
        "id": f"{org}/splent_feature_{short}",
        "org": org,
        "repo": f"splent_feature_{short}",
        "short": short,
        "version": "v1.0.0",
        "project_version": "1.0.0",
        "description": f"{short} feature",
        "archetype": "full",
        "category": None,
        "tags": [],
        "env": None,
        "provides": {"routes": list(provides_routes), "services": [], "models": []},
        "requires": {"features": list(requires), "env_vars": [], "signals": []},
        "extensible": {},
        "docker": None,
        "refinement": None,
        "source": "github",
        "used_by": [],
        "pypi": None,
        "github": None,
    }


class TestFeatureEntry:
    def test_parses_contract(self):
        entry = marketplace.feature_entry_from_pyproject(
            CONTRACT_TOML,
            org="splent-io",
            repo="splent_feature_events",
            source="github",
            version="v0.3.0",
        )
        assert entry["id"] == "splent-io/splent_feature_events"
        assert entry["short"] == "events"
        assert entry["archetype"] == "full"
        assert entry["category"] == "content"
        assert entry["tags"] == ["events", "cms"]
        assert entry["requires"]["features"] == ["auth"]
        assert entry["provides"]["models"] == ["Event"]
        assert entry["description"] == "Events for the public site"
        assert entry["version"] == "v0.3.0"
        assert entry["project_version"] == "0.3.0"


class TestComputedRelations:
    def test_used_by(self):
        entries = [
            _entry("auth"),
            _entry("admin", requires=["auth"]),
            _entry("notes", requires=["auth"]),
        ]
        marketplace.compute_used_by(entries)
        by_short = {e["short"]: e for e in entries}
        assert by_short["auth"]["used_by"] == ["admin", "notes"]
        assert by_short["admin"]["used_by"] == []

    def test_collisions(self):
        entries = [
            _entry("recaptcha", provides_routes=["/admin/captcha"]),
            _entry("cloudflare", provides_routes=["/admin/captcha"]),
            _entry("events", provides_routes=["/events"]),
        ]
        collisions = marketplace.compute_collisions(entries)
        assert len(collisions) == 1
        assert collisions[0]["kind"] == "route"
        assert collisions[0]["item"] == "/admin/captcha"
        assert collisions[0]["features"] == ["cloudflare", "recaptcha"]

    def test_dependency_closure_is_transitive(self):
        index = {
            "features": [
                _entry("auth"),
                _entry("projects", requires=["auth"]),
                _entry("research", requires=["projects"]),
            ]
        }
        assert marketplace.dependency_closure(index, "research") == [
            "auth",
            "projects",
        ]


class TestUvlParser:
    def test_features_and_presence(self):
        model = marketplace.parse_uvl_structure(UVL_TEXT)
        feats = model["features"]
        assert feats["theme"]["presence"] == "mandatory"
        assert feats["events"]["presence"] == "optional"
        assert feats["events"]["package"] == "splent_feature_events"
        assert feats["events"]["org"] == "splent-io"

    def test_alternative_groups(self):
        model = marketplace.parse_uvl_structure(UVL_TEXT)
        groups = model["alternative_groups"]
        assert len(groups) == 1
        assert groups[0]["owner"] == "session"
        assert groups[0]["members"] == ["session_filesystem", "session_redis"]
        assert model["features"]["session_redis"]["group"] == "session"

    def test_constraints(self):
        model = marketplace.parse_uvl_structure(UVL_TEXT)
        assert ["events", "media"] in model["constraints"]
        assert ["session_redis", "redis"] in model["constraints"]


SPL_METADATA = """\
[spl]
name = "demo_spl"
description = "Demo SPL"

[spl.uvl]
mirror = "uvlhub.io"
doi = "10.1234/demo"
file = "demo_spl.uvl"
"""


class TestBuildSpls:
    """build_spls auto-fetches missing UVLs (spl:fetch logic) best-effort:
    a fetch failure must warn — never fail the build, never stay silent —
    and a locally present UVL must mean zero network."""

    def _catalog_spl(self, tmp_path, name="demo_spl"):
        spl_dir = tmp_path / "splent_catalog" / name
        spl_dir.mkdir(parents=True)
        (spl_dir / "metadata.toml").write_text(SPL_METADATA)
        return spl_dir

    def test_missing_uvl_invokes_fetch_and_failure_warns_without_propagating(
        self, tmp_path, monkeypatch, capsys
    ):
        from splent_cli.commands.spl import spl_utils

        spl_dir = self._catalog_spl(tmp_path)
        calls = []

        def _failing_fetch(spl_name, metadata, target):
            calls.append((spl_name, target))
            raise RuntimeError("UVLHub is down")

        monkeypatch.setattr(spl_utils, "_fetch_uvl", _failing_fetch)

        spls = marketplace.build_spls(str(tmp_path))

        # The fetch WAS attempted, with the right SPL and target path.
        assert calls == [("demo_spl", str(spl_dir / "demo_spl.uvl"))]
        # The failure did not propagate: the SPL is indexed without a model.
        assert len(spls) == 1
        assert spls[0]["name"] == "demo_spl"
        assert spls[0]["model"] is None
        assert spls[0]["uvl"]["doi"] == "10.1234/demo"
        # And it was warned about — regressions never disappear silently.
        out = capsys.readouterr().out
        assert "could not fetch UVL for demo_spl" in out
        assert "UVLHub is down" in out

    def test_local_uvl_present_means_zero_network(self, tmp_path, monkeypatch):
        from splent_cli.commands.spl import spl_utils

        spl_dir = self._catalog_spl(tmp_path)
        (spl_dir / "demo_spl.uvl").write_text(UVL_TEXT)
        calls = []

        def _boom(*args, **kwargs):
            calls.append(args)
            raise AssertionError("fetch attempted with a local UVL present")

        monkeypatch.setattr(spl_utils, "_fetch_uvl", _boom)

        spls = marketplace.build_spls(str(tmp_path))

        assert calls == []
        assert len(spls) == 1
        assert spls[0]["model"] is not None
        assert "theme" in spls[0]["model"]["features"]

    def test_successful_fetch_writes_uvl_and_parses_model(
        self, tmp_path, monkeypatch
    ):
        from splent_cli.commands.spl import spl_utils

        spl_dir = self._catalog_spl(tmp_path)

        def _fake_fetch(spl_name, metadata, target):
            Path(target).write_text(UVL_TEXT)

        monkeypatch.setattr(spl_utils, "_fetch_uvl", _fake_fetch)

        spls = marketplace.build_spls(str(tmp_path))

        assert (spl_dir / "demo_spl.uvl").is_file()
        assert len(spls) == 1
        model = spls[0]["model"]
        assert model is not None
        assert model["features"]["events"]["presence"] == "optional"
        assert ["events", "media"] in model["constraints"]


class TestSearch:
    def _index(self):
        auth = _entry("auth")
        auth["archetype"] = "full"
        mail = _entry("mail")
        mail["archetype"] = "service"
        mail["category"] = "integration"
        mail["tags"] = ["email"]
        mail["provides"]["services"] = ["MailService"]
        admin = _entry("admin", requires=["auth"])
        return {"features": [auth, mail, admin]}

    def test_query_matches_name(self):
        results = marketplace.search_features(self._index(), "mai")
        assert [e["short"] for e in results] == ["mail"]

    def test_filter_archetype(self):
        results = marketplace.search_features(self._index(), archetype="service")
        assert [e["short"] for e in results] == ["mail"]

    def test_filter_provides(self):
        results = marketplace.search_features(self._index(), provides="MailService")
        assert [e["short"] for e in results] == ["mail"]

    def test_filter_requires(self):
        results = marketplace.search_features(self._index(), requires="auth")
        assert [e["short"] for e in results] == ["admin"]

    def test_filter_tag_and_category(self):
        results = marketplace.search_features(
            self._index(), tag="email", category="integration"
        )
        assert [e["short"] for e in results] == ["mail"]


class TestFindFeature:
    def test_by_short_and_full(self):
        index = {"features": [_entry("auth")]}
        assert marketplace.find_feature(index, "auth")["short"] == "auth"
        assert marketplace.find_feature(index, "splent_feature_auth")["short"] == "auth"
        assert (
            marketplace.find_feature(index, "splent-io/splent_feature_auth")["short"]
            == "auth"
        )
        assert marketplace.find_feature(index, "other-org/splent_feature_auth") is None
        assert marketplace.find_feature(index, "nope") is None


class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        index = marketplace.assemble_index(
            [_entry("auth")], [], sources={"orgs": ["splent-io"], "repos": [], "workspace": False}
        )
        path = tmp_path / "index.json"
        marketplace.save_index(index, path)
        loaded = marketplace.load_index_file(path)
        assert loaded is not None
        assert loaded["schema"] == marketplace.INDEX_SCHEMA
        assert loaded["features"][0]["short"] == "auth"

    def test_load_rejects_wrong_schema(self, tmp_path):
        path = tmp_path / "index.json"
        path.write_text(json.dumps({"schema": 99}))
        assert marketplace.load_index_file(path) is None

    def test_build_workspace_features(self, tmp_path):
        feat = tmp_path / "splent_feature_demo"
        feat.mkdir()
        (feat / "pyproject.toml").write_text(CONTRACT_TOML)
        entries = marketplace.build_workspace_features(str(tmp_path))
        assert len(entries) == 1
        assert entries[0]["source"] == "workspace"
        assert entries[0]["short"] == "demo"


class TestResolveIndexUrl:
    def _valid_index(self):
        return {"schema": marketplace.INDEX_SCHEMA, "features": [], "spls": []}

    def test_fetches_and_caches_from_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv(marketplace.INDEX_URL_ENV, "https://example.test/index.json")
        monkeypatch.setattr(
            marketplace, "fetch_remote_index", lambda url: self._valid_index()
        )
        index, origin = marketplace.resolve_index(str(tmp_path))
        assert origin == "url"
        assert index["schema"] == marketplace.INDEX_SCHEMA
        # Cached for next time
        assert marketplace.index_cache_path(str(tmp_path)).is_file()

    def test_falls_back_to_cache_when_url_down(self, tmp_path, monkeypatch):
        cache = marketplace.index_cache_path(str(tmp_path))
        marketplace.save_index(self._valid_index(), cache)
        monkeypatch.setenv(marketplace.INDEX_URL_ENV, "https://example.test/index.json")
        monkeypatch.setattr(marketplace, "fetch_remote_index", lambda url: None)
        index, origin = marketplace.resolve_index(str(tmp_path), refresh=True)
        assert origin == "cache"
        assert index is not None

    def test_nothing_available(self, tmp_path, monkeypatch):
        monkeypatch.delenv(marketplace.INDEX_URL_ENV, raising=False)
        index, origin = marketplace.resolve_index(str(tmp_path))
        assert index is None
        assert origin == ""

    def test_fetch_remote_index_rejects_html(self, monkeypatch):
        import io

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        import urllib.request

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _Resp(b"<html>nope</html>")
        )
        assert marketplace.fetch_remote_index("https://x.test/i.json") is None


class TestBuildRemoteFeatures:
    def _wire(self, monkeypatch, *, repos, tags, files, pypi=None):
        from splent_cli.services import registry

        monkeypatch.setattr(registry, "list_org_repos", lambda org, token=None: repos)
        monkeypatch.setattr(
            registry,
            "list_tags",
            lambda org, repo, token=None, max_pages=50: tags.get(repo, []),
        )
        monkeypatch.setattr(
            registry,
            "fetch_file",
            lambda org, repo, path, ref=None, token=None: files.get(repo),
        )
        monkeypatch.setattr(
            registry, "fetch_repo", lambda org, repo, token=None: None
        )
        monkeypatch.setattr(
            registry, "pypi_versions", lambda pkg: (pypi or {}).get(pkg, [])
        )

    def test_indexes_released_features_and_reports_problems(self, monkeypatch):
        repos = [
            {"name": "splent_feature_auth", "html_url": "u", "archived": False},
            {"name": "splent_feature_wip", "html_url": "u", "archived": False},
            {"name": "not_a_feature", "archived": False},
        ]
        self._wire(
            monkeypatch,
            repos=repos,
            tags={"splent_feature_auth": ["v1.0.0"], "splent_feature_wip": []},
            files={"splent_feature_auth": CONTRACT_TOML},
            pypi={"splent_feature_auth": ["1.0.0"]},
        )
        entries, problems = marketplace.build_remote_features(
            ["splent-io"], [], token=None
        )
        assert [e["repo"] for e in entries] == ["splent_feature_auth"]
        assert entries[0]["version"] == "v1.0.0"
        assert entries[0]["pypi"]["published"] is True
        assert any("no released tags" in p for p in problems)

    def test_rate_limit_propagates_instead_of_truncating(self, monkeypatch):
        from splent_cli.services import registry

        repos = [{"name": "splent_feature_auth", "html_url": "u", "archived": False}]

        def _limited(*a, **k):
            raise registry.RegistryError(
                "GitHub API error (HTTP 403)", status=403, rate_limited=True
            )

        monkeypatch.setattr(registry, "list_org_repos", lambda org, token=None: repos)
        monkeypatch.setattr(registry, "list_tags", _limited)
        import pytest

        with pytest.raises(registry.RegistryError):
            marketplace.build_remote_features(["splent-io"], [], token=None)
