"""Where SPL models live, now that splent_catalog does not.

The catalog was a git repository whose only real content was three
metadata.toml files mapping a name to a DOI, and it had to be cloned before
anything could resolve a model. These tests pin the replacement:

  * three homes, tried in a fixed order (working copy, cache, UVLHub);
  * the DOI travels in the product's own pyproject.toml, so a product plus
    UVLHub is enough and neither the catalog nor the marketplace is ever
    consulted;
  * deleting the cache costs a download and nothing else;
  * a model that has never been published still works.

No network anywhere: the one function that would reach out is
``spl_store.fetch_uvl``, and every test that exercises it substitutes the
transport.
"""

import pytest

from splent_cli.services import spl_store
from tests.conftest import make_spl_cache_entry, make_spl_working_copy


UVL = "features\n\tdemo_spl\n"
DOI = "10.5281/zenodo.21610307"
CONCEPT = "10.5281/zenodo.21610306"


def _product(
    workspace, name="demo_app", spl="demo_spl", doi=DOI, version="v2", concept=CONCEPT
):
    """A product declaring an SPL and pinning its model."""
    path = workspace / name
    path.mkdir(parents=True, exist_ok=True)
    block = ""
    if doi is not None:
        block = (
            "\n[tool.splent.spl_model]\n"
            'mirror = "uvlhub.io"\n'
            f'doi = "{doi}"\n'
            f'concept_doi = "{concept or ""}"\n'
            f'version = "{version or ""}"\n'
        )
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n\n[tool.splent]\nspl = "{spl}"\n{block}'
    )
    return path


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


class TestWhereModelsLive:
    def test_working_copy_mirrors_the_feature_directory_convention(self):
        # splent_feature_auth is to a feature what splent_spl_cms is to an SPL.
        assert spl_store.working_copy_dirname("cms_spl") == "splent_spl_cms"
        assert spl_store.working_copy_dirname("marketplace_spl") == (
            "splent_spl_marketplace"
        )

    def test_a_name_without_the_suffix_is_left_alone(self):
        assert spl_store.working_copy_dirname("demo") == "splent_spl_demo"

    def test_the_unstripped_directory_is_still_found(self, tmp_path):
        """A repo cloned under the literal name must not be orphaned."""
        literal = tmp_path / "splent_spl_cms_spl"
        literal.mkdir()
        assert spl_store.working_copy(tmp_path, "cms_spl") == literal

    def test_cache_is_keyed_like_a_cached_feature(self, tmp_path):
        assert spl_store.cache_dir(tmp_path, "demo_spl", "v2").name == "demo_spl@v2"
        assert spl_store.cache_dir(tmp_path, "demo_spl", None).name == "demo_spl"

    def test_cache_lives_under_the_same_root_as_cached_features(self, tmp_path):
        cached = spl_store.cache_dir(tmp_path, "demo_spl", "v2")
        assert cached.parent == tmp_path / ".splent_cache" / "spls"


# ---------------------------------------------------------------------------
# Resolution order
# ---------------------------------------------------------------------------


class TestResolutionOrder:
    def test_working_copy_wins_over_cache(self, tmp_path):
        make_spl_cache_entry(tmp_path, "demo_spl", "features\n\tcached\n")
        copy = make_spl_working_copy(tmp_path, "demo_spl", "features\n\tedited\n")

        resolved = spl_store.find_uvl(tmp_path, "demo_spl")

        assert resolved == str(copy / "demo_spl.uvl")

    def test_cache_is_used_when_there_is_no_working_copy(self, tmp_path):
        cached = make_spl_cache_entry(tmp_path, "demo_spl", UVL)
        assert spl_store.find_uvl(tmp_path, "demo_spl") == str(cached / "demo_spl.uvl")

    def test_a_versioned_cache_entry_serves_a_pin_for_that_version(self, tmp_path):
        cached = make_spl_cache_entry(tmp_path, "demo_spl", UVL, version="v2")
        found = spl_store.find_uvl(tmp_path, "demo_spl", version="v2")
        assert found == str(cached / "demo_spl.uvl")

    def test_a_versioned_cache_entry_also_serves_an_unversioned_lookup(self, tmp_path):
        """One fetch, then offline, even when the pin records no version."""
        make_spl_cache_entry(tmp_path, "demo_spl", UVL, version="v2")
        assert spl_store.find_uvl(tmp_path, "demo_spl") is not None

    def test_nothing_on_disk_and_no_doi_is_an_error_naming_every_place(self, tmp_path):
        with pytest.raises(Exception) as exc:
            spl_store.resolve_uvl(tmp_path, "demo_spl")
        message = str(exc.value)
        assert "splent_spl_demo" in message
        assert ".splent_cache" in message
        assert "spl:create" in message

    def test_a_leftover_catalog_entry_is_pointed_at_the_migration(self, tmp_path):
        (tmp_path / "splent_catalog" / "demo_spl").mkdir(parents=True)
        with pytest.raises(Exception) as exc:
            spl_store.resolve_uvl(tmp_path, "demo_spl")
        assert "spl:migrate-catalog" in str(exc.value)


# ---------------------------------------------------------------------------
# The bootstrap answer: the DOI travels with the product
# ---------------------------------------------------------------------------


class TestTheProductCarriesTheDoi:
    def test_a_product_alone_is_enough_to_know_what_to_download(self, tmp_path):
        """No catalog, no working copy, no cache. Just the product."""
        _product(tmp_path)

        pin = spl_store.read_pin(tmp_path, "demo_spl")

        assert pin.doi == DOI
        assert pin.concept_doi == CONCEPT
        assert pin.version == "v2"
        assert pin.fetchable

    def test_the_concept_doi_is_what_makes_drift_reportable(self, tmp_path):
        """It never changes across versions, so it identifies the line."""
        _product(tmp_path, version="v2")
        pin = spl_store.read_pin(tmp_path, "demo_spl")
        assert pin.concept_doi == CONCEPT
        assert pin.concept_doi != pin.doi

    def test_the_asking_product_outranks_another_product(self, tmp_path):
        _product(tmp_path, name="other_app", doi="10.5281/zenodo.OTHER")
        _product(tmp_path, name="mine", doi=DOI)

        pin = spl_store.read_pin(tmp_path, "demo_spl", product="mine")

        assert pin.doi == DOI

    def test_the_working_copy_outranks_a_product_pin(self, tmp_path):
        """An author editing the model knows more than anything downstream."""
        _product(tmp_path, doi="10.5281/zenodo.OLD")
        make_spl_working_copy(tmp_path, "demo_spl", UVL, doi=DOI)

        assert spl_store.read_pin(tmp_path, "demo_spl").doi == DOI

    def test_a_product_pin_outranks_a_stale_cache_record(self, tmp_path):
        make_spl_cache_entry(tmp_path, "demo_spl", UVL, doi="10.5281/zenodo.OLD")
        _product(tmp_path, doi=DOI)

        assert spl_store.read_pin(tmp_path, "demo_spl").doi == DOI

    def test_a_model_with_no_doi_still_resolves_from_the_working_copy(self, tmp_path):
        """Authoring something never published must keep working."""
        copy = make_spl_working_copy(tmp_path, "demo_spl", UVL)
        pin = spl_store.read_pin(tmp_path, "demo_spl")

        assert not pin.fetchable
        assert spl_store.resolve_uvl(tmp_path, "demo_spl") == str(copy / "demo_spl.uvl")


# ---------------------------------------------------------------------------
# Fetching, with the transport replaced
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


@pytest.fixture
def fake_get(monkeypatch):
    """Replace requests.get so no test can reach a real service."""
    calls = []

    def _get(url, **kwargs):
        calls.append(url)
        return FakeResponse(UVL)

    import requests

    monkeypatch.setattr(requests, "get", _get)
    return calls


class TestFetching:
    def test_a_fetch_writes_only_into_the_cache(self, tmp_path, fake_get):
        _product(tmp_path)
        pin = spl_store.read_pin(tmp_path, "demo_spl")

        path = spl_store.fetch_uvl(tmp_path, pin, quiet=True)

        assert path == str(
            tmp_path / ".splent_cache" / "spls" / "demo_spl@v2" / "demo_spl.uvl"
        )
        assert len(fake_get) == 1
        assert DOI in fake_get[0]

    def test_the_second_resolution_is_offline(self, tmp_path, fake_get):
        _product(tmp_path)
        spl_store.resolve_uvl(tmp_path, "demo_spl", quiet=True)
        spl_store.resolve_uvl(tmp_path, "demo_spl", quiet=True)

        assert len(fake_get) == 1

    def test_deleting_the_cache_costs_exactly_one_download(self, tmp_path, fake_get):
        from splent_cli.utils.cache_utils import rmtree_force

        _product(tmp_path)
        spl_store.resolve_uvl(tmp_path, "demo_spl", quiet=True)
        rmtree_force(tmp_path / ".splent_cache")
        spl_store.resolve_uvl(tmp_path, "demo_spl", quiet=True)

        assert len(fake_get) == 2

    def test_a_working_copy_is_never_overwritten_by_a_fetch(self, tmp_path, fake_get):
        copy = make_spl_working_copy(
            tmp_path, "demo_spl", "features\n\tmine\n", doi=DOI, version="v2"
        )
        pin = spl_store.read_pin(tmp_path, "demo_spl")

        spl_store.fetch_uvl(tmp_path, pin, quiet=True)

        assert (copy / "demo_spl.uvl").read_text() == "features\n\tmine\n"

    def test_the_remote_filename_is_not_assumed_to_match_the_name(
        self, tmp_path, fake_get
    ):
        """sample_splent_spl publishes sample_splent_app.uvl, and always did."""
        pin = spl_store.SplPin(
            name="sample_splent_spl",
            doi="10.5281/zenodo.20837624",
            file="sample_splent_app.uvl",
        )
        spl_store.fetch_uvl(tmp_path, pin, quiet=True)

        assert "sample_splent_app.uvl" in fake_get[0]
        # On disk it is still named after the SPL. The directory holding it is
        # keyed on the DOI, because this pin records no version.
        assert (
            spl_store.cache_dir(
                tmp_path, "sample_splent_spl", None, "10.5281/zenodo.20837624"
            )
            / "sample_splent_spl.uvl"
        ).is_file()

    def test_a_pin_with_no_doi_refuses_rather_than_guessing(self, tmp_path):
        with pytest.raises(Exception) as exc:
            spl_store.fetch_uvl(tmp_path, spl_store.SplPin(name="demo_spl"))
        assert "nothing to download" in str(exc.value)

    def test_product_uvl_is_offline_by_default(self, tmp_path, fake_get):
        """Boot-time callers must not silently wait on UVLHub."""
        _product(tmp_path)
        assert spl_store.product_uvl(tmp_path, "demo_app") is None
        assert fake_get == []


# ---------------------------------------------------------------------------
# Writing the pin
# ---------------------------------------------------------------------------


class TestWritingThePin:
    def test_a_pin_is_appended_and_reads_back(self, tmp_path):
        product = _product(tmp_path, doi=None)
        pyproject = product / "pyproject.toml"

        spl_store.write_product_pin(
            pyproject,
            spl_store.SplPin(
                name="demo_spl", doi=DOI, concept_doi=CONCEPT, version="v2"
            ),
        )

        pin = spl_store.read_product_pin(tmp_path, "demo_app")
        assert (pin.doi, pin.concept_doi, pin.version) == (DOI, CONCEPT, "v2")

    def test_rewriting_replaces_the_block_instead_of_stacking_them(self, tmp_path):
        product = _product(tmp_path)
        pyproject = product / "pyproject.toml"

        spl_store.write_product_pin(
            pyproject, spl_store.SplPin(name="demo_spl", doi="10.5281/zenodo.NEW")
        )
        spl_store.write_product_pin(
            pyproject, spl_store.SplPin(name="demo_spl", doi="10.5281/zenodo.NEWER")
        )

        text = pyproject.read_text()
        assert text.count("[tool.splent.spl_model]") == 1
        assert spl_store.read_product_pin(tmp_path, "demo_app").doi == (
            "10.5281/zenodo.NEWER"
        )

    def test_the_rest_of_the_file_survives(self, tmp_path):
        product = _product(tmp_path, doi=None)
        pyproject = product / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text()
            + '\n[tool.setuptools]\npackage-dir = { "" = "src" }\n'
        )

        spl_store.write_product_pin(
            pyproject, spl_store.SplPin(name="demo_spl", doi=DOI)
        )

        import tomllib

        data = tomllib.loads(pyproject.read_text())
        assert data["tool"]["setuptools"]["package-dir"] == {"": "src"}
        assert data["tool"]["splent"]["spl"] == "demo_spl"
        assert data["tool"]["splent"]["spl_model"]["doi"] == DOI

    def test_a_block_in_the_middle_does_not_swallow_the_next_table(self, tmp_path):
        """Replacing must not eat the table that follows it."""
        product = _product(tmp_path)
        pyproject = product / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text() + "\n[tool.setuptools]\nzip-safe = false\n"
        )

        spl_store.write_product_pin(
            pyproject, spl_store.SplPin(name="demo_spl", doi="10.5281/zenodo.NEW")
        )

        import tomllib

        data = tomllib.loads(pyproject.read_text())
        assert data["tool"]["setuptools"]["zip-safe"] is False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestKnownSpls:
    def test_all_three_homes_and_the_products_contribute(self, tmp_path):
        make_spl_working_copy(tmp_path, "edited_spl", UVL)
        make_spl_cache_entry(tmp_path, "cached_spl", UVL)
        _product(tmp_path, name="app_a", spl="pinned_spl")

        assert spl_store.known_spls(tmp_path) == [
            "cached_spl",
            "edited_spl",
            "pinned_spl",
        ]

    def test_a_versioned_cache_entry_is_reported_by_its_name(self, tmp_path):
        make_spl_cache_entry(tmp_path, "demo_spl", UVL, version="v2")
        assert spl_store.known_spls(tmp_path) == ["demo_spl"]

    def test_an_empty_workspace_knows_nothing(self, tmp_path):
        assert spl_store.known_spls(tmp_path) == []

    def test_products_pinning_reports_who_is_on_what(self, tmp_path):
        _product(tmp_path, name="app_a", version="v2")
        _product(tmp_path, name="app_b", version="v5")

        assert spl_store.products_pinning(tmp_path, "demo_spl") == [
            ("app_a", "v2"),
            ("app_b", "v5"),
        ]


class TestTwoProductsPinningTwoVersions:
    """The normal state right after spl:publish moves one product forward.

    Both writers of pins record an empty version, so every pin in practice is
    version-less. Keying the cache on the name alone made all of them share one
    directory: whichever product resolved first won, and the others silently
    derived from a model they do not pin.
    """

    OLD = "10.5281/zenodo.20837624"
    NEW = "10.5281/zenodo.99999999"

    @pytest.fixture
    def two_dois(self, monkeypatch):
        """Serve different bytes per DOI, so the wrong model is detectable."""
        calls = []

        def _get(url, **kwargs):
            calls.append(url)
            marker = "old" if "20837624" in url else "new"
            return FakeResponse(f"features\n\tdemo_spl_{marker}\n")

        import requests

        monkeypatch.setattr(requests, "get", _get)
        return calls

    def _pin(self, doi):
        return spl_store.SplPin(name="demo_spl", doi=doi, version=None)

    def test_each_doi_gets_its_own_cache_directory(self, tmp_path, two_dois):
        old = spl_store.fetch_uvl(tmp_path, self._pin(self.OLD), quiet=True)
        new = spl_store.fetch_uvl(tmp_path, self._pin(self.NEW), quiet=True)

        assert old != new
        assert len(two_dois) == 2

    def test_a_product_reads_the_model_it_pins_not_the_one_cached_first(
        self, tmp_path, two_dois
    ):
        # app_old resolves first and fills the cache. app_new must not be
        # served that file: it pins a different DOI.
        _product(tmp_path, name="app_old", doi=self.OLD, version="")
        _product(tmp_path, name="app_new", doi=self.NEW, version="")

        old_path = spl_store.resolve_uvl(
            tmp_path, "demo_spl", product="app_old", quiet=True
        )
        new_path = spl_store.resolve_uvl(
            tmp_path, "demo_spl", product="app_new", quiet=True
        )

        assert "demo_spl_old" in open(old_path).read()
        assert "demo_spl_new" in open(new_path).read()

    def test_a_cache_directory_recording_another_doi_is_not_a_candidate(
        self, tmp_path, two_dois
    ):
        # The pre-existing bare <name>/ directory from an older CLI, holding a
        # different record. Serving it would be the same silent substitution.
        legacy = spl_store.cache_dir(tmp_path, "demo_spl", None)
        legacy.mkdir(parents=True)
        (legacy / "demo_spl.uvl").write_text("features\n\tdemo_spl_old\n")
        spl_store.write_metadata(legacy, self._pin(self.OLD))

        assert spl_store.find_uvl(tmp_path, "demo_spl", doi=self.NEW) is None
        assert spl_store.find_uvl(tmp_path, "demo_spl", doi=self.OLD) == str(
            legacy / "demo_spl.uvl"
        )

    def test_a_cache_directory_that_records_no_doi_is_still_usable(self, tmp_path):
        # Written before DOIs were tracked. Refusing it would strand caches
        # that are probably fine, so it is accepted.
        legacy = spl_store.cache_dir(tmp_path, "demo_spl", None)
        legacy.mkdir(parents=True)
        (legacy / "demo_spl.uvl").write_text(UVL)

        assert spl_store.find_uvl(tmp_path, "demo_spl", doi=self.NEW) == str(
            legacy / "demo_spl.uvl"
        )

    def test_the_doi_slug_is_a_single_readable_path_segment(self):
        assert spl_store.doi_slug("10.5281/zenodo.20837624") == (
            "10.5281_zenodo.20837624"
        )
        assert "/" not in spl_store.doi_slug("10.5281/zenodo.1")


class TestCatalogMigrationIsNamed:
    def _catalog_entry(self, workspace, name="demo_spl"):
        entry = workspace / "splent_catalog" / name
        entry.mkdir(parents=True)
        (entry / "metadata.toml").write_text(
            f'[spl]\nname = "{name}"\n\n[spl.uvl]\ndoi = "{DOI}"\n'
        )
        return entry

    def test_an_unmigrated_workspace_is_reported_as_pending(self, tmp_path):
        self._catalog_entry(tmp_path)

        assert spl_store.catalog_migration_pending(tmp_path) == ["demo_spl"]

    def test_a_model_a_product_already_pins_is_not_pending(self, tmp_path):
        self._catalog_entry(tmp_path)
        _product(tmp_path)

        assert spl_store.catalog_migration_pending(tmp_path) == []

    def test_a_workspace_with_no_catalog_has_nothing_pending(self, tmp_path):
        assert spl_store.catalog_migration_pending(tmp_path) == []
