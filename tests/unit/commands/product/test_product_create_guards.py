"""
Tests for product:create — hardened safety guards.

Covered hardened behaviors (first), then a couple of happy-path cases:
  * chown to uid/gid 1000 is guarded: a PermissionError (or any OSError) from
    os.chown is swallowed so the command does NOT crash. The generated tree is
    still produced and the success message printed.
  * Creating a product that ALREADY exists exits NON-zero (so CI detects it),
    instead of silently overwriting / exiting 0. With --force it overwrites.

No real docker / git / network / templates: the Jinja render + raw-copy helpers
are stubbed to drop empty files, and os.chown is patched at the boundary.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from splent_cli.commands.product.product_create import make_product
from tests.conftest import make_spl_working_copy

_RENDER = "splent_cli.commands.product.product_create.render_and_write_file"
_COPY = "splent_cli.commands.product.product_create.copy_raw_file"
_CHOWN = "splent_cli.commands.product.product_create.os.chown"
_PLATFORM = "splent_cli.commands.product.product_create.sys.platform"


def _mock_render(env, template_name, filename, ctx):
    """Stub Jinja render: create an empty file without needing real templates."""
    import os

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    open(filename, "w").close()


def _mock_copy(template_name, filename):
    """Stub raw copy: create an empty file without needing real source assets."""
    import os

    os.makedirs(os.path.dirname(filename), exist_ok=True)
    open(filename, "w").close()


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_spl(workspace, name="demo_spl", **kwargs):
    """Create a working copy of an SPL so make_product can derive from it via
    --spl without any prompt or network fetch."""
    make_spl_working_copy(workspace, name, "features\n  Root\n", **kwargs)
    return name


def _invoke_create(runner, name, spl, *, chown_side_effect=None, extra_args=None):
    """Invoke product:create on Linux with helpers stubbed.

    do_chown is gated on sys.platform.startswith('linux'), so we force linux to
    actually exercise the chown path; chown_side_effect controls its behavior.
    """
    args = [name, "--spl", spl] + (extra_args or [])
    with patch(_PLATFORM, "linux"):
        with patch(_RENDER, side_effect=_mock_render):
            with patch(_COPY, side_effect=_mock_copy):
                with patch(_CHOWN, side_effect=chown_side_effect) as chown:
                    result = runner.invoke(make_product, args)
    return result, chown


def _assert_clean_stderr(result):
    err = result.stderr or ""
    assert "Traceback" not in err
    assert "CalledProcessError" not in err


# ---------------------------------------------------------------------------
# Hardened: chown is guarded (non-fatal)
# ---------------------------------------------------------------------------


class TestChownGuard:
    def test_permission_error_on_chown_is_non_fatal(self, runner, workspace):
        """os.chown raising PermissionError must NOT crash the command."""
        spl = _make_spl(workspace)
        result, chown = _invoke_create(
            runner, "myprod", spl, chown_side_effect=PermissionError("not permitted")
        )

        assert result.exit_code == 0, result.stderr
        _assert_clean_stderr(result)
        assert "✅" in result.output
        # chown was actually attempted (guard is around the call, not a no-op).
        assert chown.called
        # The product tree was still created despite the chown failure.
        assert (workspace / "myprod" / "pyproject.toml").is_file()

    def test_oserror_on_chown_is_non_fatal(self, runner, workspace):
        """A generic OSError from chown (e.g. unsupported on host) is swallowed."""
        spl = _make_spl(workspace)
        result, _ = _invoke_create(
            runner, "myprod", spl, chown_side_effect=OSError("op not supported")
        )

        assert result.exit_code == 0, result.stderr
        _assert_clean_stderr(result)
        assert (workspace / "myprod").is_dir()

    def test_chown_targets_uid_gid_1000(self, runner, workspace):
        """When chown succeeds it is invoked with uid/gid 1000 on the tree."""
        spl = _make_spl(workspace)
        result, chown = _invoke_create(runner, "myprod", spl, chown_side_effect=None)

        assert result.exit_code == 0, result.stderr
        assert chown.called
        for call in chown.call_args_list:
            args = call.args
            assert args[1] == 1000
            assert args[2] == 1000


# ---------------------------------------------------------------------------
# Hardened: existing product must fail loudly (non-zero) without --force
# ---------------------------------------------------------------------------


class TestAlreadyExistsGuard:
    def test_existing_product_exits_nonzero(self, runner, workspace):
        """Re-creating an existing product must exit NON-zero so CI catches it."""
        spl = _make_spl(workspace)
        (workspace / "myprod").mkdir()
        (workspace / "myprod" / "sentinel.txt").write_text("keep me")

        result, _ = _invoke_create(runner, "myprod", spl)

        assert result.exit_code != 0
        _assert_clean_stderr(result)
        assert "already exists" in result.stderr
        # The pre-existing content must be left intact (no destructive overwrite).
        assert (workspace / "myprod" / "sentinel.txt").read_text() == "keep me"

    def test_force_overwrites_existing(self, runner, workspace):
        """--force replaces the existing product tree and succeeds."""
        spl = _make_spl(workspace)
        (workspace / "myprod").mkdir()
        stale = workspace / "myprod" / "stale.txt"
        stale.write_text("old")

        result, _ = _invoke_create(runner, "myprod", spl, extra_args=["--force"])

        assert result.exit_code == 0, result.stderr
        _assert_clean_stderr(result)
        # Old content removed by the rmtree-then-recreate path.
        assert not stale.exists()
        assert (workspace / "myprod" / "pyproject.toml").is_file()


# ---------------------------------------------------------------------------
# Core happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_creates_product_tree(self, runner, workspace):
        spl = _make_spl(workspace)
        result, _ = _invoke_create(runner, "newprod", spl)

        assert result.exit_code == 0, result.stderr
        base = workspace / "newprod"
        assert base.is_dir()
        assert (base / "docker").is_dir()
        assert (base / "src" / "newprod").is_dir()
        assert (base / "pyproject.toml").is_file()
        assert "✅" in result.output

    def test_the_generated_pyproject_carries_the_model_doi(self, runner, workspace):
        """A product must be able to resolve its model with no catalog.

        The template is stubbed here, so what is asserted is the render
        context: those are the values the real template writes into
        [tool.splent.spl_model].
        """
        captured = {}

        def _capture(env, template_name, filename, ctx):
            captured.update(ctx)
            _mock_render(env, template_name, filename, ctx)

        spl = _make_spl(
            workspace,
            doi="10.5281/zenodo.4242",
            concept_doi="10.5281/zenodo.4241",
            version="v2",
        )
        with patch(_PLATFORM, "linux"):
            with patch(_RENDER, side_effect=_capture):
                with patch(_COPY, side_effect=_mock_copy):
                    with patch(_CHOWN):
                        result = runner.invoke(make_product, ["newprod", "--spl", spl])

        assert result.exit_code == 0, result.stderr
        assert captured["spl_name"] == "demo_spl"
        assert captured["spl_doi"] == "10.5281/zenodo.4242"
        assert captured["spl_concept_doi"] == "10.5281/zenodo.4241"
        assert captured["spl_version"] == "v2"

    def test_an_unpublished_model_warns_that_a_clone_cannot_resolve_it(
        self, runner, workspace
    ):
        spl = _make_spl(workspace)  # working copy, no DOI
        result, _ = _invoke_create(runner, "newprod", spl)

        assert result.exit_code == 0, result.stderr
        assert "No DOI recorded" in result.output

    def test_a_doi_can_be_supplied_for_a_model_never_seen_here(
        self, runner, workspace, monkeypatch
    ):
        """No working copy, no cache, no catalog. Just a name and a DOI."""
        captured = {}

        def _capture(env, template_name, filename, ctx):
            captured.update(ctx)
            _mock_render(env, template_name, filename, ctx)

        class _Response:
            status_code = 200
            text = "features\n  Root\n"

        import requests

        monkeypatch.setattr(requests, "get", lambda url, **k: _Response())

        with patch(_PLATFORM, "linux"):
            with patch(_RENDER, side_effect=_capture):
                with patch(_COPY, side_effect=_mock_copy):
                    with patch(_CHOWN):
                        result = runner.invoke(
                            make_product,
                            [
                                "newprod",
                                "--spl",
                                "remote_spl",
                                "--spl-doi",
                                "10.5281/zenodo.9999",
                            ],
                        )

        assert result.exit_code == 0, result.stderr
        assert captured["spl_doi"] == "10.5281/zenodo.9999"
        # It was fetched into the cache, so the next command works offline.
        from splent_cli.services import spl_store

        assert (
            spl_store.cache_dir(
                str(workspace), "remote_spl", None, "10.5281/zenodo.9999"
            )
            / "remote_spl.uvl"
        ).is_file()

    def test_aborts_when_product_already_selected(self, runner, workspace, monkeypatch):
        """requires_detached: command refuses to run with a product selected."""
        monkeypatch.setenv("SPLENT_APP", "some_app")
        spl = _make_spl(workspace)
        result, _ = _invoke_create(runner, "newprod", spl)

        assert result.exit_code != 0
        # requires_detached emits via click.secho (stdout); message must surface.
        combined = (result.output + (result.stderr or "")).lower()
        assert "product is currently selected" in combined


# ---------------------------------------------------------------------------
# The pin product:create writes must actually be usable
# ---------------------------------------------------------------------------


class TestTheWrittenPinResolves:
    """product:create is one of the two writers of a pin.

    A pin is the whole contract: a clone of the product repository plus UVLHub
    has to be enough to fetch the model. The remote filename is part of that
    contract and is not derivable from the SPL name (sample_splent_spl
    publishes sample_splent_app.uvl), so a pin that drops it resolves to a URL
    that 404s and the product cannot be derived on a clean machine.

    These render the real pyproject template rather than the stub, because the
    seam that broke was between the command's context and that template.
    """

    DOI = "10.5281/zenodo.20837624"
    REMOTE = "sample_splent_app.uvl"

    def _real_pyproject_render(self, captured):
        """Stub every template except pyproject.toml, which is rendered for real."""
        import os

        from splent_cli.commands.product.product_create import (
            render_and_write_file,
            setup_jinja_env,
        )

        def _render(env, template_name, filename, ctx):
            captured.update(ctx)
            if template_name.endswith("product_pyproject.toml.j2"):
                render_and_write_file(setup_jinja_env(), template_name, filename, ctx)
                return
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            open(filename, "w").close()

        return _render

    def _create(self, runner, workspace, captured):
        make_spl_working_copy(
            workspace,
            "sample_splent_spl",
            "features\n  Root\n",
            doi=self.DOI,
            file=self.REMOTE,
        )
        with patch(_PLATFORM, "linux"):
            with patch(_RENDER, side_effect=self._real_pyproject_render(captured)):
                with patch(_COPY, side_effect=_mock_copy):
                    with patch(_CHOWN):
                        return runner.invoke(
                            make_product,
                            ["probe_sample", "--spl", "sample_splent_spl"],
                        )

    def test_the_remote_filename_reaches_the_template_context(self, runner, workspace):
        captured = {}

        result = self._create(runner, workspace, captured)

        assert result.exit_code == 0, result.stderr
        assert captured["spl_file"] == self.REMOTE

    def test_the_written_pin_asks_uvlhub_for_the_right_file(self, runner, workspace):
        from splent_cli.services import spl_store

        result = self._create(runner, workspace, {})
        assert result.exit_code == 0, result.stderr

        pin = spl_store.read_product_pin(str(workspace), "probe_sample")

        assert pin.doi == self.DOI
        # The whole point: not "sample_splent_spl.uvl", which 404s.
        assert pin.remote_file == self.REMOTE

    def test_the_download_url_built_from_that_pin_names_the_real_file(
        self, runner, workspace
    ):
        from splent_cli.commands.uvl.uvl_utils import resolve_uvlhub_raw_url
        from splent_cli.services import spl_store

        assert self._create(runner, workspace, {}).exit_code == 0
        pin = spl_store.read_product_pin(str(workspace), "probe_sample")

        url = resolve_uvlhub_raw_url(pin.mirror, pin.doi, pin.remote_file)

        assert self.REMOTE in url
        assert "sample_splent_spl.uvl" not in url
