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


def _make_spl(workspace, name="demo_spl"):
    """Create a catalog SPL with a direct .uvl file so make_product can derive
    from it via --spl without any prompt or network fetch."""
    spl_dir = workspace / "splent_catalog" / name
    spl_dir.mkdir(parents=True, exist_ok=True)
    (spl_dir / f"{name}.uvl").write_text("features\n  Root\n")
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

    def test_aborts_when_product_already_selected(self, runner, workspace, monkeypatch):
        """requires_detached: command refuses to run with a product selected."""
        monkeypatch.setenv("SPLENT_APP", "some_app")
        spl = _make_spl(workspace)
        result, _ = _invoke_create(runner, "newprod", spl)

        assert result.exit_code != 0
        # requires_detached emits via click.secho (stdout); message must surface.
        combined = (result.output + (result.stderr or "")).lower()
        assert "product is currently selected" in combined
