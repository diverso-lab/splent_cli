"""
Tests for the cache:prune command.
"""
import pytest
from click.testing import CliRunner

from splent_cli.commands.cache.cache_prune import cache_prune


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_cache(workspace, namespace, name, version=None):
    dir_name = f"{name}@{version}" if version else name
    path = workspace / ".splent_cache" / "features" / namespace / dir_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_product(workspace, name, features=None):
    product_dir = workspace / name
    product_dir.mkdir(parents=True, exist_ok=True)
    features_list = "\n".join(f'  "{f}",' for f in (features or []))
    (product_dir / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0.0"\n\n'
        f'[project.optional-dependencies]\nfeatures = [\n{features_list}\n]\n'
    )
    return product_dir


# ---------------------------------------------------------------------------
# Empty cache
# ---------------------------------------------------------------------------

class TestEmptyCache:
    def test_empty_cache_shows_info(self, runner, workspace):
        result = runner.invoke(cache_prune, ["--yes"])
        assert result.exit_code == 0
        assert "empty" in result.output.lower()


# ---------------------------------------------------------------------------
# Nothing to prune
# ---------------------------------------------------------------------------

class TestNothingToPrune:
    def test_all_referenced_nothing_pruned(self, runner, workspace):
        _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        _make_product(workspace, "test_app", ["splent_io/splent_feature_auth@v1.0.0"])

        result = runner.invoke(cache_prune, ["--yes"])
        assert result.exit_code == 0
        assert "Nothing to prune" in result.output


# ---------------------------------------------------------------------------
# Orphan pruning
# ---------------------------------------------------------------------------

class TestOrphanPruning:
    def test_prunes_unreferenced_entry_with_yes(self, runner, workspace):
        entry = _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")

        result = runner.invoke(cache_prune, ["--yes"])
        assert result.exit_code == 0
        assert "Pruned" in result.output
        assert not entry.exists()

    def test_cancel_at_prompt_keeps_entry(self, runner, workspace):
        entry = _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")

        result = runner.invoke(cache_prune, [], input="n\n")
        assert result.exit_code == 0
        assert "Cancelled" in result.output
        assert entry.exists()

    def test_confirm_at_prompt_prunes(self, runner, workspace):
        entry = _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")

        result = runner.invoke(cache_prune, [], input="y\n")
        assert result.exit_code == 0
        assert "Pruned" in result.output
        assert not entry.exists()

    def test_shows_orphan_name_before_confirmation(self, runner, workspace):
        _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")

        result = runner.invoke(cache_prune, [], input="n\n")
        assert "splent_feature_auth" in result.output

    def test_only_orphans_removed_referenced_kept(self, runner, workspace):
        orphan = _make_cache(workspace, "splent_io", "splent_feature_orphan", "v1.0.0")
        referenced = _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        _make_product(workspace, "test_app", ["splent_io/splent_feature_auth@v1.0.0"])

        result = runner.invoke(cache_prune, ["--yes"])
        assert result.exit_code == 0
        assert not orphan.exists()
        assert referenced.exists()

    def test_editable_entry_pruned_when_unreferenced(self, runner, workspace):
        entry = _make_cache(workspace, "splent_io", "splent_feature_auth")  # editable

        result = runner.invoke(cache_prune, ["--yes"])
        assert result.exit_code == 0
        assert not entry.exists()


# ---------------------------------------------------------------------------
# Broken symlink cleanup
# ---------------------------------------------------------------------------

class TestBrokenSymlinks:
    def test_broken_symlinks_removed_after_prune(self, runner, workspace):
        _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")
        features_dir = workspace / "test_app" / "features" / "splent_io"
        features_dir.mkdir(parents=True)
        broken = features_dir / "splent_feature_auth"
        broken.symlink_to(workspace / "nonexistent")

        result = runner.invoke(cache_prune, ["--yes"])
        assert result.exit_code == 0
        assert "symlink" in result.output.lower()

    def test_no_symlinks_message_when_clean(self, runner, workspace):
        _make_cache(workspace, "splent_io", "splent_feature_auth", "v1.0.0")

        result = runner.invoke(cache_prune, ["--yes"])
        assert result.exit_code == 0
        assert "No broken symlinks" in result.output
