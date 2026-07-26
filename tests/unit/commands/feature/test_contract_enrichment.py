"""Contract enrichment: archetype, service_proxy inference, preserved fields.

Covers the marketplace-facing additions to [tool.splent.contract]:
  - archetype is inferred and written
  - service_proxy("XService") calls become requires.features via the map
  - category / tags / manually added requires.features survive --write
"""

import tomllib
from pathlib import Path

from splent_cli.commands.feature.feature_release import (
    build_service_map,
    infer_contract,
    write_contract,
)


def _feature(tmp_path, name="splent_feature_demo", org="splent_io"):
    src = tmp_path / name / "src" / org / name
    src.mkdir(parents=True)
    return tmp_path / name, src


def _pyproject(feature_root: Path, extra: str = "") -> Path:
    path = feature_root / "pyproject.toml"
    path.write_text(
        "[project]\n"
        f'name = "{feature_root.name}"\n'
        'version = "0.1.0"\n'
        f"{extra}"
    )
    return path


class TestArchetypeInference:
    def test_full_when_models_exist(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "models.py").write_text(
            "class Thing(db.Model):\n    name = db.Column(db.String)\n"
        )
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        assert contract["archetype"] == "full"

    def test_light_when_routes_only(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "routes.py").write_text("@bp.route('/x')\ndef x(): pass\n")
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        assert contract["archetype"] == "light"

    def test_config_when_empty(self, tmp_path):
        root, src = _feature(tmp_path)
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        assert contract["archetype"] == "config"


class TestServiceProxyInference:
    def test_proxy_call_becomes_requirement(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "routes.py").write_text(
            '@bp.route("/send")\n'
            "def send():\n"
            '    service_proxy("MailService").send()\n'
        )
        contract = infer_contract(
            str(root),
            "splent_io",
            root.name,
            service_map={"MailService": "mail"},
        )
        assert "mail" in contract["requires_features"]

    def test_own_service_is_not_a_requirement(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "services.py").write_text(
            "class DemoService(BaseService):\n    def go(self):\n        return 1\n"
        )
        (src / "routes.py").write_text('svc = service_proxy("DemoService")\n')
        contract = infer_contract(
            str(root),
            "splent_io",
            root.name,
            service_map={"DemoService": "demo"},
        )
        assert contract["requires_features"] == []

    def test_unknown_service_is_ignored(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "routes.py").write_text('svc = service_proxy("MysteryService")\n')
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        assert contract["requires_features"] == []


class TestRegisteredServices:
    def test_plain_registered_class_is_provided(self, tmp_path):
        # MailService extends nothing — only register_service reveals it.
        root, src = _feature(tmp_path)
        (src / "services.py").write_text("class DemoService:\n    pass\n")
        (src / "__init__.py").write_text(
            "def init_feature(app):\n"
            '    register_service(app, "DemoService", DemoService)\n'
        )
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        assert contract["services"] == ["DemoService"]

    def test_source_registration_feeds_service_map(self, tmp_path):
        root = tmp_path / "splent_feature_mail"
        src = root / "src" / "splent_io" / "splent_feature_mail"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text(
            'register_service(app, "MailService", MailService)\n'
        )
        # Stale contract: provides.services is empty.
        (root / "pyproject.toml").write_text(
            "[tool.splent.contract.provides]\nservices = []\n"
        )
        assert build_service_map(str(tmp_path)) == {"MailService": "mail"}


class TestBuildServiceMap:
    def test_maps_services_to_shorts(self, tmp_path):
        for short, svc in (("mail", "MailService"), ("media", "MediaService")):
            root = tmp_path / f"splent_feature_{short}"
            root.mkdir()
            (root / "pyproject.toml").write_text(
                "[tool.splent.contract.provides]\n" f'services = ["{svc}"]\n'
            )
        mapping = build_service_map(str(tmp_path))
        assert mapping == {"MailService": "mail", "MediaService": "media"}

    def test_ambiguous_services_are_dropped(self, tmp_path):
        for short in ("one", "two"):
            root = tmp_path / f"splent_feature_{short}"
            root.mkdir()
            (root / "pyproject.toml").write_text(
                '[tool.splent.contract.provides]\nservices = ["SharedService"]\n'
            )
        assert build_service_map(str(tmp_path)) == {}


class TestWriteContractPreservation:
    def test_writes_archetype(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "models.py").write_text("class T(db.Model):\n    x = db.Column(db.Int)\n")
        path = _pyproject(root)
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        write_contract(str(path), contract, root.name)
        data = tomllib.loads(path.read_text())
        assert data["tool"]["splent"]["contract"]["archetype"] == "full"

    def test_preserves_category_tags_and_manual_requires(self, tmp_path):
        root, src = _feature(tmp_path)
        path = _pyproject(
            root,
            "\n[tool.splent.contract]\n"
            'description = "Demo feature for tests"\n'
            'category = "content"\n'
            'tags = ["demo", "test"]\n'
            "\n[tool.splent.contract.requires]\n"
            'features_manual = ["media"]\n',
        )
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        write_contract(str(path), contract, root.name)

        data = tomllib.loads(path.read_text())
        written = data["tool"]["splent"]["contract"]
        assert written["description"] == "Demo feature for tests"
        assert written["category"] == "content"
        assert written["tags"] == ["demo", "test"]
        # The manual dependency survives regeneration even though no code
        # references media at all, and stays declared as manual.
        assert written["requires"]["features"] == ["media"]
        assert written["requires"]["features_manual"] == ["media"]

    def test_manual_and_inferred_requires_merge(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "routes.py").write_text(
            'service_proxy("MailService").send()\n'
        )
        path = _pyproject(
            root,
            "\n[tool.splent.contract.requires]\n" 'features_manual = ["media"]\n',
        )
        contract = infer_contract(
            str(root), "splent_io", root.name, service_map={"MailService": "mail"}
        )
        write_contract(str(path), contract, root.name)
        data = tomllib.loads(path.read_text())
        assert data["tool"]["splent"]["contract"]["requires"]["features"] == [
            "mail",
            "media",
        ]
        assert data["tool"]["splent"]["contract"]["requires"]["features_manual"] == [
            "media"
        ]

    def test_stale_inferred_requires_expire(self, tmp_path):
        # A dependency that was auto-inferred in a previous write must DROP
        # from requires.features once the code stops using it — only
        # features_manual entries are permanent.
        root, src = _feature(tmp_path)
        (src / "routes.py").write_text('service_proxy("MailService").send()\n')
        path = _pyproject(root)
        service_map = {"MailService": "mail"}
        contract = infer_contract(
            str(root), "splent_io", root.name, service_map=service_map
        )
        write_contract(str(path), contract, root.name)
        assert tomllib.loads(path.read_text())["tool"]["splent"]["contract"][
            "requires"
        ]["features"] == ["mail"]

        (src / "routes.py").write_text("def nothing(): pass\n")
        contract = infer_contract(
            str(root), "splent_io", root.name, service_map=service_map
        )
        write_contract(str(path), contract, root.name)
        assert (
            tomllib.loads(path.read_text())["tool"]["splent"]["contract"][
                "requires"
            ]["features"]
            == []
        )

    def test_real_shape_pyproject_survives_regeneration(self, tmp_path):
        # Literal shape of a real workspace feature (research_projects):
        # build-system + setuptools tables + cli_version BEFORE the generated
        # block, and a refinement table with sub-tables AFTER it. Everything
        # must survive two regenerations byte-identically.
        root, src = _feature(tmp_path)
        (src / "models.py").write_text(
            "class Thing(db.Model):\n    x = db.Column(db.Int)\n"
        )
        path = root / "pyproject.toml"
        path.write_text(
            '[build-system]\n'
            'requires = ["setuptools>=80.3.1", "wheel"]\n'
            'build-backend = "setuptools.build_meta"\n'
            "\n"
            "[project]\n"
            f'name = "{root.name}"\n'
            'version = "0.2.0"\n'
            'readme = "README.md"\n'
            'requires-python = ">=3.13"\n'
            "\n"
            "[tool.setuptools]\n"
            'package-dir = { "" = "src" }\n'
            "include-package-data = true\n"
            "\n"
            "[tool.setuptools.packages.find]\n"
            'where = ["src"]\n'
            'exclude = ["*.tests", "*.tests.*"]\n'
            "\n"
            "[tool.splent]\n"
            'cli_version = "1.11.0"\n'
            "\n"
            "# ── Feature Contract (auto-generated) ────────────────────────────────────────\n"
            "# Do not edit manually — re-run `splent feature:contract --write` to refresh.\n"
            "[tool.splent.contract]\n"
            'description = "Research projects extending projects"\n'
            "\n"
            "[tool.splent.contract.provides]\n"
            "routes     = []\n"
            "\n"
            "[tool.splent.contract.requires]\n"
            'features = ["projects"]\n'
            "env_vars = []\n"
            "signals  = []\n"
            "\n"
            "[tool.splent.refinement]\n"
            'refines = "splent_feature_projects"\n'
            "\n"
            "[tool.splent.refinement.extends]\n"
            'models = [{target = "Project", mixin = "ResearchProjectMixin"}]\n'
            "\n"
            "[tool.splent.refinement.overrides]\n"
            'templates = ["projects/list.html", "projects/detail.html"]\n'
        )

        for _ in range(2):
            contract = infer_contract(
                str(root), "splent_io", root.name, service_map={}
            )
            write_contract(str(path), contract, root.name)
            if _ == 0:
                first = path.read_text()

        text = path.read_text()
        assert text == first  # second write is byte-identical
        data = tomllib.loads(text)
        assert data["build-system"]["build-backend"] == "setuptools.build_meta"
        assert data["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
        assert data["tool"]["splent"]["cli_version"] == "1.11.0"
        contract_written = data["tool"]["splent"]["contract"]
        assert contract_written["description"] == "Research projects extending projects"
        assert contract_written["archetype"] == "full"
        refinement = data["tool"]["splent"]["refinement"]
        assert refinement["refines"] == "splent_feature_projects"
        assert refinement["extends"]["models"][0]["mixin"] == "ResearchProjectMixin"
        assert refinement["overrides"]["templates"] == [
            "projects/list.html",
            "projects/detail.html",
        ]
        assert text.count("[tool.splent.refinement]") == 1
        assert text.count("# ── Feature Contract") == 1

    def test_rewrite_is_idempotent(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "models.py").write_text("class T(db.Model):\n    x = db.Column(db.Int)\n")
        path = _pyproject(root)
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        write_contract(str(path), contract, root.name)
        first = path.read_text()
        contract = infer_contract(str(root), "splent_io", root.name, service_map={})
        write_contract(str(path), contract, root.name)
        assert path.read_text() == first


class TestSoftDependencies:
    def test_guarded_usage_is_optional_not_required(self, tmp_path):
        # try/except around the usage = graceful degradation = soft dep.
        root, src = _feature(tmp_path)
        (src / "routes.py").write_text(
            'events_service = service_proxy("EventsService")\n'
            "def index():\n"
            "    try:\n"
            "        upcoming = events_service.list_published()[:3]\n"
            "    except Exception:\n"
            "        upcoming = []\n"
        )
        contract = infer_contract(
            str(root), "splent_io", root.name,
            service_map={"EventsService": "events"},
        )
        assert contract["requires_features"] == []
        assert contract["requires_features_optional"] == ["events"]

    def test_bare_usage_of_assigned_proxy_is_hard(self, tmp_path):
        root, src = _feature(tmp_path)
        (src / "routes.py").write_text(
            'svc = service_proxy("MailService")\n'
            "def send():\n"
            "    svc.send()\n"
        )
        contract = infer_contract(
            str(root), "splent_io", root.name, service_map={"MailService": "mail"}
        )
        assert contract["requires_features"] == ["mail"]
        assert contract["requires_features_optional"] == []
