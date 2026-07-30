"""The spl:* commands after splent_catalog.

Covers the commands whose whole job changed when the catalog went away:

  * spl:create writes a working copy at the workspace root, not a catalog entry;
  * spl:list and spl:fetch find a model through a product's pin, which is the
    case the catalog used to be the only answer to;
  * spl:pin records the DOI in the product, which is what makes deriving need
    the product plus UVLHub and nothing else;
  * spl:migrate-catalog moves the old contents across and does NOT delete the
    directory it read.

Nothing here touches the network. Every command runs detached except spl:pin,
which by design operates on the selected product.
"""

import tomllib

import pytest
from click.testing import CliRunner

from splent_cli.commands.spl.spl_create import spl_create
from splent_cli.commands.spl.spl_fetch import spl_fetch
from splent_cli.commands.spl.spl_list import spl_list
from splent_cli.commands.spl.spl_migrate_catalog import spl_migrate_catalog
from splent_cli.commands.spl.spl_outdated import spl_outdated
from splent_cli.commands.spl.spl_pin import spl_pin
from splent_cli.services import spl_store
from tests.conftest import make_spl_cache_entry, make_spl_working_copy


UVL = "features\n\tdemo_spl\n"
DOI = "10.5281/zenodo.21610307"
CONCEPT = "10.5281/zenodo.21610306"


def _out(result):
    return result.output + (result.stderr or "")


def _product(workspace, name="demo_app", spl="demo_spl", doi=DOI, version="v2"):
    path = workspace / name
    path.mkdir(parents=True, exist_ok=True)
    block = ""
    if doi is not None:
        block = (
            "\n[tool.splent.spl_model]\n"
            'mirror = "uvlhub.io"\n'
            f'doi = "{doi}"\n'
            f'concept_doi = "{CONCEPT}"\n'
            f'version = "{version}"\n'
        )
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n\n[tool.splent]\nspl = "{spl}"\n{block}'
    )
    return path


@pytest.fixture
def no_network(monkeypatch):
    """Any HTTP call from a command under test is a test failure."""
    import requests

    def _boom(*args, **kwargs):
        raise AssertionError(f"network call attempted: {args}")

    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests, "post", _boom)


# ---------------------------------------------------------------------------
# spl:create
# ---------------------------------------------------------------------------


class TestSplCreate:
    def test_a_new_model_lands_beside_the_feature_repositories(
        self, workspace, runner, no_network
    ):
        result = runner.invoke(spl_create, ["cms_spl"])

        assert result.exit_code == 0, _out(result)
        copy = workspace / "splent_spl_cms"
        assert (copy / "cms_spl.uvl").is_file()
        assert (copy / "metadata.toml").is_file()
        assert not (workspace / "splent_catalog").exists()

    def test_the_model_itself_is_tracked_unlike_in_the_catalog(
        self, workspace, runner, no_network
    ):
        """The catalog gitignored *.uvl. A working copy is the source of truth."""
        runner.invoke(spl_create, ["cms_spl"])

        gitignore = (workspace / "splent_spl_cms" / ".gitignore").read_text()
        assert "*.uvl\n" not in gitignore
        assert "*.uvl.bak" in gitignore

    def test_a_brand_new_model_has_no_doi_and_that_is_fine(
        self, workspace, runner, no_network
    ):
        runner.invoke(spl_create, ["cms_spl"])

        pin = spl_store.read_pin(str(workspace), "cms_spl")
        assert pin.doi is None
        assert spl_store.find_uvl(str(workspace), "cms_spl") is not None

    def test_creating_twice_refuses(self, workspace, runner, no_network):
        runner.invoke(spl_create, ["cms_spl"])
        result = runner.invoke(spl_create, ["cms_spl"])
        assert result.exit_code == 1
        assert "already exists" in _out(result)


# ---------------------------------------------------------------------------
# spl:list
# ---------------------------------------------------------------------------


class TestSplList:
    def test_a_model_known_only_through_a_product_pin_is_listed(
        self, workspace, runner, no_network
    ):
        """This is exactly what the catalog used to be required for."""
        _product(workspace)

        result = runner.invoke(spl_list, [])

        assert result.exit_code == 0, _out(result)
        assert "demo_spl" in result.output
        assert "not fetched" in result.output

    def test_the_three_homes_are_distinguished(self, workspace, runner, no_network):
        make_spl_working_copy(workspace, "edited_spl", UVL)
        make_spl_cache_entry(workspace, "cached_spl", UVL)

        result = runner.invoke(spl_list, [])

        assert "editing" in result.output
        assert "cached" in result.output

    def test_an_empty_workspace_says_so_without_failing(
        self, workspace, runner, no_network
    ):
        result = runner.invoke(spl_list, [])
        assert result.exit_code == 0
        assert "spl:create" in result.output


# ---------------------------------------------------------------------------
# spl:fetch
# ---------------------------------------------------------------------------


class TestSplFetch:
    def test_the_doi_comes_from_the_product_and_the_file_lands_in_the_cache(
        self, workspace, runner, monkeypatch
    ):
        _product(workspace)
        seen = []

        class _Response:
            status_code = 200
            text = UVL

        def _get(url, **kwargs):
            seen.append(url)
            return _Response()

        import requests

        monkeypatch.setattr(requests, "get", _get)

        result = runner.invoke(spl_fetch, ["demo_spl"])

        assert result.exit_code == 0, _out(result)
        assert DOI in seen[0]
        assert (
            workspace / ".splent_cache" / "spls" / "demo_spl@v2" / "demo_spl.uvl"
        ).is_file()

    def test_no_doi_anywhere_is_a_clean_error_with_no_network(
        self, workspace, runner, no_network
    ):
        result = runner.invoke(spl_fetch, ["demo_spl"])

        assert result.exit_code != 0
        assert "Traceback" not in _out(result)
        assert "spl:create" in _out(result)

    def test_it_warns_that_a_working_copy_will_win_anyway(
        self, workspace, runner, monkeypatch
    ):
        make_spl_working_copy(workspace, "demo_spl", UVL, doi=DOI)

        class _Response:
            status_code = 200
            text = UVL

        import requests

        monkeypatch.setattr(requests, "get", lambda url, **k: _Response())

        result = runner.invoke(spl_fetch, ["demo_spl"])

        assert result.exit_code == 0, _out(result)
        assert "you are editing" in _out(result)


# ---------------------------------------------------------------------------
# spl:pin
# ---------------------------------------------------------------------------


class TestSplPin:
    def test_it_records_the_doi_in_the_product(
        self, workspace, runner, monkeypatch, no_network
    ):
        _product(workspace, doi=None)
        make_spl_working_copy(
            workspace, "demo_spl", UVL, doi=DOI, concept_doi=CONCEPT, version="v3"
        )
        monkeypatch.setenv("SPLENT_APP", "demo_app")

        result = runner.invoke(spl_pin, [])

        assert result.exit_code == 0, _out(result)
        data = tomllib.loads((workspace / "demo_app" / "pyproject.toml").read_text())
        model = data["tool"]["splent"]["spl_model"]
        assert model["doi"] == DOI
        assert model["concept_doi"] == CONCEPT
        assert model["version"] == "v3"

    def test_an_explicit_doi_wins(self, workspace, runner, monkeypatch, no_network):
        _product(workspace)
        monkeypatch.setenv("SPLENT_APP", "demo_app")

        result = runner.invoke(spl_pin, ["--doi", "10.5281/zenodo.NEW"])

        assert result.exit_code == 0, _out(result)
        assert (
            spl_store.read_product_pin(str(workspace), "demo_app").doi
            == "10.5281/zenodo.NEW"
        )

    def test_pinning_nothing_warns_that_a_clone_will_not_resolve(
        self, workspace, runner, monkeypatch, no_network
    ):
        _product(workspace, doi=None)
        make_spl_working_copy(workspace, "demo_spl", UVL)
        monkeypatch.setenv("SPLENT_APP", "demo_app")

        result = runner.invoke(spl_pin, [])

        assert result.exit_code == 0, _out(result)
        assert "will not be able to resolve" in _out(result)


# ---------------------------------------------------------------------------
# spl:outdated
# ---------------------------------------------------------------------------


class TestSplOutdated:
    def test_local_drift_is_reported_without_any_network(
        self, workspace, runner, no_network
    ):
        _product(workspace, doi="10.5281/zenodo.OLD", version="v2")
        make_spl_working_copy(workspace, "demo_spl", UVL, doi=DOI, version="v5")

        result = runner.invoke(spl_outdated, [])

        assert result.exit_code == 0, _out(result)
        assert "pins v2" in result.output
        assert "v5" in result.output
        assert "1 behind" in result.output

    def test_matching_dois_are_up_to_date(self, workspace, runner, no_network):
        _product(workspace, doi=DOI, version="v2")
        make_spl_working_copy(workspace, "demo_spl", UVL, doi=DOI, version="v2")

        result = runner.invoke(spl_outdated, [])

        assert "up to date" in result.output
        assert "0 behind" in result.output

    def test_remote_uses_the_concept_doi(self, workspace, runner, monkeypatch):
        import splent_cli.commands.spl.spl_outdated as mod

        _product(workspace, doi=DOI, version="v2")
        asked = []

        def _latest(mirror, concept_doi):
            asked.append(concept_doi)
            return ("10.5281/zenodo.NEWEST", "v5")

        monkeypatch.setattr(mod, "latest_version_doi", _latest)

        result = runner.invoke(spl_outdated, ["--remote"])

        assert asked == [CONCEPT]
        assert "the line is on v5" in result.output

    def test_an_unreadable_remote_answer_is_never_reported_as_up_to_date(
        self, workspace, runner, monkeypatch
    ):
        import splent_cli.commands.spl.spl_outdated as mod

        _product(workspace, doi=DOI, version="v2")
        monkeypatch.setattr(mod, "latest_version_doi", lambda *a: None)

        result = runner.invoke(spl_outdated, ["--remote"])

        assert "did not say" in result.output
        assert "up to date" not in result.output


# ---------------------------------------------------------------------------
# spl:migrate-catalog
# ---------------------------------------------------------------------------


def _catalog(workspace, name="demo_spl", doi=DOI, with_uvl=True):
    spl_dir = workspace / "splent_catalog" / name
    spl_dir.mkdir(parents=True, exist_ok=True)
    (spl_dir / "metadata.toml").write_text(
        "[spl]\n"
        f'name = "{name}"\n'
        'description = "A catalogued SPL"\n'
        "\n[spl.uvl]\n"
        'mirror = "uvlhub.io"\n'
        f'doi = "{doi}"\n'
        f'file = "{name}.uvl"\n'
    )
    if with_uvl:
        (spl_dir / f"{name}.uvl").write_text(UVL)
    return spl_dir


class TestMigrateCatalog:
    def test_the_model_is_cached_and_the_doi_lands_in_the_product(
        self, workspace, runner, no_network
    ):
        _catalog(workspace)
        _product(workspace, doi=None)

        result = runner.invoke(spl_migrate_catalog, [])

        assert result.exit_code == 0, _out(result)
        pin = spl_store.read_product_pin(str(workspace), "demo_app")
        assert (
            spl_store.cache_dir(str(workspace), "demo_spl", pin.version, pin.doi)
            / "demo_spl.uvl"
        ).is_file()
        assert pin.doi == DOI

    def test_the_catalog_directory_is_left_alone(self, workspace, runner, no_network):
        spl_dir = _catalog(workspace)
        _product(workspace, doi=None)

        runner.invoke(spl_migrate_catalog, [])

        assert spl_dir.is_dir()
        assert (spl_dir / "metadata.toml").is_file()
        assert (spl_dir / "demo_spl.uvl").is_file()
        assert "was not touched" in _out(runner.invoke(spl_migrate_catalog, []))

    def test_after_migrating_the_model_resolves_with_no_catalog_read(
        self, workspace, runner, no_network
    ):
        """The point of the whole exercise, asserted end to end."""
        _catalog(workspace)
        _product(workspace, doi=None)
        runner.invoke(spl_migrate_catalog, [])

        import shutil

        shutil.rmtree(workspace / "splent_catalog")

        assert spl_store.product_uvl(str(workspace), "demo_app") is not None

    def test_dry_run_writes_nothing(self, workspace, runner, no_network):
        _catalog(workspace)
        _product(workspace, doi=None)
        before = (workspace / "demo_app" / "pyproject.toml").read_bytes()

        result = runner.invoke(spl_migrate_catalog, ["--dry-run"])

        assert result.exit_code == 0, _out(result)
        assert "would" in result.output
        assert not (workspace / ".splent_cache").exists()
        assert (workspace / "demo_app" / "pyproject.toml").read_bytes() == before

    def test_running_it_twice_changes_nothing_the_second_time(
        self, workspace, runner, no_network
    ):
        _catalog(workspace)
        _product(workspace, doi=None)
        runner.invoke(spl_migrate_catalog, [])
        after_first = (workspace / "demo_app" / "pyproject.toml").read_bytes()

        result = runner.invoke(spl_migrate_catalog, [])

        assert result.exit_code == 0, _out(result)
        assert "already records this DOI" in result.output
        assert (workspace / "demo_app" / "pyproject.toml").read_bytes() == after_first

    def test_a_catalog_entry_with_no_local_model_still_pins_the_doi(
        self, workspace, runner, no_network
    ):
        """The .uvl files were gitignored, so a fresh clone had none."""
        _catalog(workspace, with_uvl=False)
        _product(workspace, doi=None)

        result = runner.invoke(spl_migrate_catalog, [])

        assert result.exit_code == 0, _out(result)
        assert "will be fetched on demand" in result.output
        assert spl_store.read_product_pin(str(workspace), "demo_app").doi == DOI

    def test_an_spl_with_no_doi_is_reported_rather_than_half_migrated(
        self, workspace, runner, no_network
    ):
        _catalog(workspace, doi="")
        _product(workspace, doi=None)

        result = runner.invoke(spl_migrate_catalog, [])

        assert result.exit_code == 0, _out(result)
        assert "Left alone" in result.output
        assert spl_store.read_product_pin(str(workspace), "demo_app").doi is None

    def test_no_catalog_is_not_an_error(self, workspace, runner, no_network):
        result = runner.invoke(spl_migrate_catalog, [])
        assert result.exit_code == 0
        assert "nothing to migrate" in result.output


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


# ---------------------------------------------------------------------------
# The unmigrated workspace names its own fix
# ---------------------------------------------------------------------------


class TestUnmigratedWorkspaceNamesTheFix:
    """Every workspace that predates the pin starts here.

    No product carries a [tool.splent.spl_model] block yet, so nothing can
    resolve a model and every command reports the same symptom. The hint used
    to exist only inside missing_model_message, which fires on a hard failure,
    so the two commands a developer actually reaches for first reported the
    symptom and never named the fix.
    """

    def test_spl_list_points_at_the_migration(self, workspace, runner, no_network):
        _catalog(workspace, with_uvl=False)
        _product(workspace, doi=None)

        result = runner.invoke(spl_list, [])

        assert result.exit_code == 0, _out(result)
        assert "demo_spl" in result.output
        assert "spl:migrate-catalog" in result.output

    def test_spl_list_stays_quiet_once_the_product_pins_the_model(
        self, workspace, runner, no_network
    ):
        _catalog(workspace, with_uvl=False)
        _product(workspace)

        result = runner.invoke(spl_list, [])

        assert "spl:migrate-catalog" not in result.output

    def test_spl_list_says_nothing_about_it_without_a_catalog(
        self, workspace, runner, no_network
    ):
        _product(workspace, doi=None)

        result = runner.invoke(spl_list, [])

        assert "spl:migrate-catalog" not in result.output

    def test_spl_info_reads_the_doi_the_catalog_still_holds(
        self, workspace, runner, no_network
    ):
        """It used to report no model and no DOI, and name the migration.

        The catalog entry has always held the DOI; nothing read it. Now that
        the resolver does, the honest answer is that the model is known and
        simply not downloaded yet, which is something the developer can act
        on. spl:list still names the migration, so the workspace is not left
        thinking it is done.
        """
        from splent_cli.commands.spl.spl_info import spl_info

        _catalog(workspace, with_uvl=False)
        _product(workspace, doi=None)

        result = runner.invoke(spl_info, ["demo_spl"])

        assert result.exit_code == 0, _out(result)
        assert "spl:fetch demo_spl" in result.output
        assert "no model and no DOI" not in result.output

    def test_spl_info_stays_quiet_once_the_model_is_pinned(
        self, workspace, runner, no_network
    ):
        from splent_cli.commands.spl.spl_info import spl_info

        _catalog(workspace, with_uvl=False)
        _product(workspace)

        result = runner.invoke(spl_info, ["demo_spl"])

        assert "spl:migrate-catalog" not in result.output
