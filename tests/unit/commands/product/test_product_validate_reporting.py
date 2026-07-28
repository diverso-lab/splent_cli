"""product:validate must not blame the constraints for a missing model.

Phase 1 wraps the whole SAT check in a bare ``except``, then reports every
failure as "Configuration is NOT satisfiable under UVL constraints". That
sentence is a verdict from the solver. When the model cannot be read the solver
never ran, so the sentence is false and it sends the reader hunting a
constraint bug that does not exist.

The catch-all is old, but it used to be nearly unreachable: the catalog was
always present, so a model was always resolvable. After the catalog went away
it became the default state of every workspace that has not run
spl:migrate-catalog yet.
"""

import pytest
from click.testing import CliRunner

from splent_cli.commands.product.product_validate import product_validate


UVL = "features\n\tdemo_spl\n"
DOI = "10.5281/zenodo.21610307"


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _out(result):
    return result.output + (result.stderr or "")


def _product(workspace, name="demo_app", spl="demo_spl", doi=None):
    path = workspace / name
    path.mkdir(parents=True, exist_ok=True)
    block = ""
    if doi is not None:
        block = (
            "\n[tool.splent.spl_model]\n"
            'mirror = "uvlhub.io"\n'
            f'doi = "{doi}"\n'
            'concept_doi = ""\n'
            'version = ""\n'
        )
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\ndependencies = []\n\n'
        f'[tool.splent]\nspl = "{spl}"\n{block}'
    )
    return path


def _catalog(workspace, name="demo_spl"):
    entry = workspace / "splent_catalog" / name
    entry.mkdir(parents=True)
    (entry / "metadata.toml").write_text(
        f'[spl]\nname = "{name}"\n\n[spl.uvl]\ndoi = "{DOI}"\n'
    )
    return entry


@pytest.fixture
def selected(workspace, monkeypatch):
    monkeypatch.setenv("SPLENT_APP", "demo_app")
    (workspace / ".env").write_text("SPLENT_APP=demo_app\n")
    return workspace


@pytest.fixture
def no_network(monkeypatch):
    import requests

    def _boom(*args, **kwargs):
        raise AssertionError(f"network call attempted: {args}")

    monkeypatch.setattr(requests, "get", _boom)
    return _boom


class TestAMissingModelIsNotAConstraintFailure:
    def test_it_does_not_claim_the_configuration_is_unsatisfiable(
        self, selected, runner, no_network
    ):
        _product(selected)

        result = runner.invoke(product_validate, ["--only", "config"])

        assert result.exit_code != 0
        assert "NOT satisfiable under UVL constraints" not in _out(result)

    def test_it_says_the_check_could_not_run(self, selected, runner, no_network):
        _product(selected)

        result = runner.invoke(product_validate, ["--only", "config"])

        assert "could not be checked" in _out(result)

    def test_it_still_prints_where_it_looked(self, selected, runner, no_network):
        _product(selected)

        result = runner.invoke(product_validate, ["--only", "config"])

        assert "No UVL model found" in _out(result)

    def test_an_unmigrated_workspace_is_told_to_migrate(
        self, selected, runner, no_network
    ):
        _catalog(selected)
        _product(selected)

        result = runner.invoke(product_validate, ["--only", "config"])

        assert "spl:migrate-catalog" in _out(result)

    def test_a_workspace_with_no_catalog_is_not_told_to_migrate(
        self, selected, runner, no_network
    ):
        _product(selected)

        result = runner.invoke(product_validate, ["--only", "config"])

        assert "spl:migrate-catalog" not in _out(result)
