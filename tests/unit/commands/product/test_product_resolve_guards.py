"""Tests for product:resolve (product_sync) cache-deletion guards.

Hardened behaviors under test (in ``_force_remove_cache_dir``):

* ``--force`` deletes a cached feature folder only after confirmation;
  declining the prompt (without ``--yes``) leaves the cache intact.
* The rmtree target must resolve to a location *inside* ``.splent_cache``;
  a path that escapes the cache root (or is the cache root itself) is refused
  and nothing is deleted.
* ``rmtree`` failures (e.g. permission errors) are surfaced as a clean,
  actionable message and a non-zero exit — never a raw traceback.

Plus a couple of happy-path tests for the command entry point.

No docker / git / network / real deletion outside tmp_path is required.
"""

import os

import pytest
from unittest.mock import patch
from click.testing import CliRunner

from splent_cli.commands.product.product_resolve import (
    product_sync,
    _force_remove_cache_dir,
)


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_cache_dir(
    workspace, namespace="splent_io", name="splent_feature_demo", version="v1.0.0"
):
    """Create a versioned cache folder inside .splent_cache and return its path."""
    cache_dir = os.path.join(
        str(workspace), ".splent_cache", "features", namespace, f"{name}@{version}"
    )
    os.makedirs(cache_dir, exist_ok=True)
    # Drop a file inside so we can prove it was (or wasn't) removed.
    with open(os.path.join(cache_dir, "marker.txt"), "w") as fh:
        fh.write("payload")
    return cache_dir


# ---------------------------------------------------------------------------
# Confirmation gating: --force only deletes after confirm / --yes
# ---------------------------------------------------------------------------


class TestForceConfirmationGating:
    def test_abort_at_prompt_leaves_cache_intact(self, tmp_path):
        cache_dir = _make_cache_dir(tmp_path)

        with (
            patch(
                "splent_cli.commands.product.product_resolve.click.confirm",
                return_value=False,
            ) as mock_confirm,
            patch(
                "splent_cli.commands.product.product_resolve.shutil.rmtree"
            ) as mock_rmtree,
        ):
            _force_remove_cache_dir(str(tmp_path), cache_dir, yes=False)

        # Prompted, declined → nothing removed, folder still there.
        mock_confirm.assert_called_once()
        mock_rmtree.assert_not_called()
        assert os.path.isdir(cache_dir)
        assert os.path.exists(os.path.join(cache_dir, "marker.txt"))

    def test_confirm_at_prompt_deletes_cache(self, tmp_path):
        cache_dir = _make_cache_dir(tmp_path)

        with patch(
            "splent_cli.commands.product.product_resolve.click.confirm",
            return_value=True,
        ) as mock_confirm:
            _force_remove_cache_dir(str(tmp_path), cache_dir, yes=False)

        mock_confirm.assert_called_once()
        # Real rmtree ran against the tmp cache dir → gone.
        assert not os.path.exists(cache_dir)

    def test_yes_flag_skips_prompt_and_deletes(self, tmp_path):
        cache_dir = _make_cache_dir(tmp_path)

        with patch(
            "splent_cli.commands.product.product_resolve.click.confirm"
        ) as mock_confirm:
            _force_remove_cache_dir(str(tmp_path), cache_dir, yes=True)

        # --yes → never prompts, deletes outright.
        mock_confirm.assert_not_called()
        assert not os.path.exists(cache_dir)


# ---------------------------------------------------------------------------
# Path validation: rmtree target must be inside .splent_cache
# ---------------------------------------------------------------------------


class TestRmtreeTargetIsInsideCache:
    def test_refuses_path_outside_cache_root(self, tmp_path):
        # A directory that exists but lives OUTSIDE .splent_cache.
        evil_dir = os.path.join(str(tmp_path), "important_data")
        os.makedirs(evil_dir, exist_ok=True)
        with open(os.path.join(evil_dir, "keep.txt"), "w") as fh:
            fh.write("do not delete")

        with patch(
            "splent_cli.commands.product.product_resolve.shutil.rmtree"
        ) as mock_rmtree:
            with pytest.raises(SystemExit) as exc:
                _force_remove_cache_dir(str(tmp_path), evil_dir, yes=True)

        assert exc.value.code == 1
        mock_rmtree.assert_not_called()
        # Outside dir untouched.
        assert os.path.exists(os.path.join(evil_dir, "keep.txt"))

    def test_refuses_traversal_escaping_cache_root(self, tmp_path):
        # Path that *looks* nested under .splent_cache but resolves outside it.
        sibling = os.path.join(str(tmp_path), "sibling")
        os.makedirs(sibling, exist_ok=True)
        escaping = os.path.join(
            str(tmp_path), ".splent_cache", "features", "..", "..", "sibling"
        )

        with patch(
            "splent_cli.commands.product.product_resolve.shutil.rmtree"
        ) as mock_rmtree:
            with pytest.raises(SystemExit) as exc:
                _force_remove_cache_dir(str(tmp_path), escaping, yes=True)

        assert exc.value.code == 1
        mock_rmtree.assert_not_called()
        assert os.path.isdir(sibling)

    def test_refuses_deleting_cache_root_itself(self, tmp_path):
        cache_root = os.path.join(str(tmp_path), ".splent_cache")
        os.makedirs(cache_root, exist_ok=True)

        with patch(
            "splent_cli.commands.product.product_resolve.shutil.rmtree"
        ) as mock_rmtree:
            with pytest.raises(SystemExit) as exc:
                _force_remove_cache_dir(str(tmp_path), cache_root, yes=True)

        assert exc.value.code == 1
        mock_rmtree.assert_not_called()
        assert os.path.isdir(cache_root)

    def test_accepts_path_inside_cache_root(self, tmp_path):
        cache_dir = _make_cache_dir(tmp_path)

        # No mock on rmtree: a valid in-cache path must actually be removed.
        _force_remove_cache_dir(str(tmp_path), cache_dir, yes=True)

        assert not os.path.exists(cache_dir)


# ---------------------------------------------------------------------------
# rmtree errors are handled cleanly (no traceback)
# ---------------------------------------------------------------------------


class TestRmtreeErrorHandling:
    def test_rmtree_oserror_surfaced_without_traceback(self, tmp_path, capsys):
        cache_dir = _make_cache_dir(tmp_path)

        with patch(
            "splent_cli.commands.product.product_resolve.shutil.rmtree",
            side_effect=OSError("Permission denied"),
        ):
            with pytest.raises(SystemExit) as exc:
                _force_remove_cache_dir(str(tmp_path), cache_dir, yes=True)

        assert exc.value.code == 1
        out = capsys.readouterr().out
        # Actionable message, no raw Python traceback / exception class names.
        assert "Failed to delete" in out
        assert "Traceback" not in out
        assert "OSError" not in out

    def test_oserror_does_not_propagate_as_raw_exception(self, tmp_path):
        # The OSError must be converted into a clean SystemExit, not bubble up.
        cache_dir = _make_cache_dir(tmp_path)

        with patch(
            "splent_cli.commands.product.product_resolve.shutil.rmtree",
            side_effect=OSError("read-only file system"),
        ):
            try:
                _force_remove_cache_dir(str(tmp_path), cache_dir, yes=True)
            except OSError:
                pytest.fail("raw OSError leaked instead of clean SystemExit")
            except SystemExit:
                pass


# ---------------------------------------------------------------------------
# Command entry point: core happy paths
# ---------------------------------------------------------------------------


class TestProductSyncHappyPath:
    def _write_pyproject(self, tmp_path, features_block):
        product_dir = tmp_path / "test_app"
        product_dir.mkdir(parents=True, exist_ok=True)
        (product_dir / "pyproject.toml").write_text(
            '[project]\nname = "test_app"\nversion = "1.0.0"\n' + features_block
        )

    def test_no_features_declared_is_clean(self, tmp_path, monkeypatch, runner):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.delenv("SPLENT_ENV", raising=False)
        self._write_pyproject(tmp_path, "")

        result = runner.invoke(product_sync, [])

        assert result.exit_code == 0
        assert "No features declared." in result.output
        assert "Traceback" not in result.output

    def test_missing_pyproject_exits_cleanly(self, tmp_path, monkeypatch, runner):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.delenv("SPLENT_ENV", raising=False)
        (tmp_path / "test_app").mkdir(parents=True, exist_ok=True)

        result = runner.invoke(product_sync, [])

        assert result.exit_code == 1
        assert "pyproject.toml not found" in result.output
        assert "Traceback" not in result.output

    def test_force_reclone_deletes_cache_then_proceeds(
        self, tmp_path, monkeypatch, runner
    ):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.delenv("SPLENT_ENV", raising=False)
        self._write_pyproject(
            tmp_path,
            '[tool.splent]\nfeatures = ["splent-io/splent_feature_demo@v1.0.0"]\n',
        )
        cache_dir = _make_cache_dir(tmp_path)

        # Replace feature_clone with a stand-in click command whose callback
        # re-creates the cache dir, simulating a successful clone. The command
        # invokes it via ``ctx.invoke(feature_clone, ...)``.
        import click as _click

        @_click.command("feature:clone")
        @_click.argument("full_name")
        def fake_clone(full_name):
            os.makedirs(cache_dir, exist_ok=True)

        with patch(
            "splent_cli.commands.product.product_resolve.feature_clone",
            fake_clone,
        ):
            result = runner.invoke(product_sync, ["--force", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Synced" in result.output
        assert "Traceback" not in result.output
