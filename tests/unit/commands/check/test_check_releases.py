"""Tests for check:releases, the workspace divergence report.

It answers the two questions that had to be worked out by hand from a published
index: which packages carry a tag that never reached PyPI, and which have a PyPI
release that was never tagged. All GitHub and PyPI access is mocked.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from splent_cli.commands.check import check_releases as mod
from splent_cli.services import registry


# ── The comparison itself ─────────────────────────────────────────────


class TestCompareChannels:
    def test_splits_the_three_buckets(self):
        result = mod.compare_channels(
            ["v1.2.0", "v1.1.0", "v1.0.0"], ["1.1.0", "1.0.0", "0.9.0"]
        )
        assert result["tag_only"] == ["v1.2.0"]
        assert result["pypi_only"] == ["0.9.0"]
        assert result["both"] == ["v1.1.0", "v1.0.0"]

    def test_v_prefix_is_not_a_difference(self):
        result = mod.compare_channels(["v1.0.0"], ["1.0.0"])
        assert result["tag_only"] == []
        assert result["pypi_only"] == []
        assert result["both"] == ["v1.0.0"]

    def test_orders_by_semver_not_by_string(self):
        result = mod.compare_channels(["v1.9.0", "v1.10.0", "v1.2.0"], [])
        assert result["tag_only"] == ["v1.10.0", "v1.9.0", "v1.2.0"]

    def test_empty_sides(self):
        assert mod.compare_channels([], []) == {
            "tag_only": [],
            "pypi_only": [],
            "both": [],
        }
        assert mod.compare_channels([], ["1.0.0"])["pypi_only"] == ["1.0.0"]

    def test_non_semver_tags_do_not_crash(self):
        result = mod.compare_channels(["nightly"], [])
        assert result["tag_only"] == ["nightly"]


# ── Discovery ─────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    def _pkg(name, extra=""):
        d = tmp_path / name
        d.mkdir()
        (d / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "1.0.0"\n' + extra
        )
        return d

    _pkg("splent_feature_alpha")
    _pkg("splent_feature_beta")
    _pkg("splent_cli")
    _pkg("innosoft_app")
    (tmp_path / "not_a_package").mkdir()
    return tmp_path


class TestDiscoverPackages:
    def test_finds_features_and_core(self, workspace):
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=True, only=None
            )
        ]
        assert names == [
            "innosoft_app",
            "splent_cli",
            "splent_feature_alpha",
            "splent_feature_beta",
        ]

    def test_products_are_included(self, workspace):
        """A product releases through the same pipeline and can diverge the same way."""
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=True, only=None
            )
        ]
        assert "innosoft_app" in names

    def test_products_can_be_excluded(self, workspace):
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=True, only=None, include_products=False
            )
        ]
        assert "innosoft_app" not in names

    def test_only_accepts_a_product_name(self, workspace):
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=True, only="innosoft_app"
            )
        ]
        assert names == ["innosoft_app"]

    def test_core_can_be_excluded(self, workspace):
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=False, only=None
            )
        ]
        assert "splent_cli" not in names

    def test_directories_without_a_pyproject_are_ignored(self, workspace):
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=True, only=None
            )
        ]
        assert "not_a_package" not in names

    def test_only_accepts_a_short_name(self, workspace):
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=True, only="alpha"
            )
        ]
        assert names == ["splent_feature_alpha"]

    def test_only_accepts_a_full_name(self, workspace):
        names = [
            n
            for n, _ in mod.discover_packages(
                str(workspace), include_core=True, only="splent_feature_beta"
            )
        ]
        assert names == ["splent_feature_beta"]


# ── The command ───────────────────────────────────────────────────────


def _run(args=None, *, tags=None, pypi=None, releases=None, repo="org/repo"):
    """Invoke the command with GitHub and PyPI answering fixed values.

    ``releases`` is the set of tag names that have a GitHub Release, or an
    exception to raise, mirroring the single paginated call the command makes.
    """
    tags = tags if tags is not None else {}
    pypi = pypi if pypi is not None else {}

    def _list_tags(org, name, token=None, quiet=True):
        value = tags.get(name, [])
        if isinstance(value, Exception):
            raise value
        return value

    def _pypi_versions(pkg, strict=False):
        value = pypi.get(pkg, [])
        if isinstance(value, Exception):
            raise value
        return value

    def _list_release_tags(org, name, token=None, max_pages=20):
        value = releases
        if isinstance(value, Exception):
            raise value
        if value is None:
            # By default every tag has a release, so the other two questions
            # can be tested in isolation.
            return set(tags.get(name, []))
        return set(value)

    with (
        patch.object(mod, "_repo_of", return_value=repo),
        patch.object(registry, "list_semver_tags", _list_tags),
        patch.object(registry, "pypi_versions", _pypi_versions),
        patch.object(registry, "list_release_tags", _list_release_tags),
    ):
        return CliRunner(mix_stderr=False).invoke(mod.check_releases, args or [])


class TestCheckReleasesCommand:
    def test_in_sync_exits_zero(self, workspace):
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v1.0.0"]},
            pypi={"splent_feature_alpha": ["1.0.0"]},
        )
        assert result.exit_code == 0
        assert "agree for all 1 package(s) checked" in result.output

    def test_tag_without_pypi_is_reported_and_fails(self, workspace):
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v0.2.1", "v0.2.0"]},
            pypi={"splent_feature_alpha": ["0.2.0"]},
        )
        assert result.exit_code == 1
        assert "tagged but not on PyPI" in result.output
        assert "v0.2.1" in result.output
        # The report hands over the exact recovery command.
        assert "splent release:resume splent_feature_alpha" in result.output

    def test_pypi_without_tag_is_reported_and_fails(self, workspace):
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v1.0.0"]},
            pypi={"splent_feature_alpha": ["1.0.0", "1.1.0"]},
        )
        assert result.exit_code == 1
        assert "on PyPI with no tag" in result.output
        assert "1.1.0" in result.output

    def test_tag_without_github_release_is_reported(self, workspace):
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v1.0.0"]},
            pypi={"splent_feature_alpha": ["1.0.0"]},
            releases=set(),
        )
        assert result.exit_code == 1
        assert "no GitHub release" in result.output

    def test_every_tag_is_checked_for_a_release_not_only_the_newest(self, workspace):
        """A feature released again after a failure used to hide the broken one."""
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v1.1.0", "v1.0.0"]},
            pypi={"splent_feature_alpha": ["1.1.0", "1.0.0"]},
            releases={"v1.1.0"},
        )
        assert result.exit_code == 1
        assert "no GitHub release" in result.output
        assert "v1.0.0" in result.output

    def test_a_failed_release_lookup_is_never_a_green_line(self, workspace):
        """The bare `except: pass` here answered "no problem" on every 401."""
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v1.0.0"]},
            pypi={"splent_feature_alpha": ["1.0.0"]},
            releases=registry.RegistryError("boom", status=401),
        )
        assert result.exit_code == 1
        assert "version(s) on both channels" not in result.output
        assert "could NOT be checked" in result.output

    def test_a_failed_pypi_read_is_never_reported_as_missing_from_pypi(self, workspace):
        """429 from PyPI used to render as "every tag is missing from PyPI"."""
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v1.6.1", "v1.6.0"]},
            pypi={
                "splent_feature_alpha": registry.RegistryError(
                    "boom", status=429, rate_limited=True
                )
            },
        )
        assert result.exit_code == 1
        assert "tagged but not on PyPI" not in result.output
        assert "release:resume" not in result.output
        assert "could NOT be checked" in result.output
        assert "PyPI is rate limiting" in result.output

    def test_a_repository_that_does_not_exist_is_not_never_released(self, workspace):
        """A renamed org or a mistyped remote is an unknown, not a clean slate."""
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": registry.RegistryError("gone", status=404)},
        )
        assert result.exit_code == 1
        assert "never released" not in result.output
        assert "could NOT be checked" in result.output

    def test_a_broken_channel_declaration_is_reported(self, workspace):
        (workspace / "splent_feature_alpha" / "pyproject.toml").write_text(
            '[project]\nname = "splent_feature_alpha"\nversion = "1.0.0"\n\n'
            '[tool.splent.release]\nchannels = ["githb"]\n'
        )
        result = _run(["--feature", "alpha"], tags={"repo": ["v1.0.0"]})
        assert result.exit_code == 1
        assert "githb" in result.output

    def test_never_released_is_not_a_divergence(self, workspace):
        result = _run(["--feature", "alpha"], tags={"repo": []}, pypi={})
        assert result.exit_code == 0
        assert "never released" in result.output

    def test_github_only_package_is_not_reported_as_missing_from_pypi(self, workspace):
        (workspace / "splent_feature_alpha" / "pyproject.toml").write_text(
            '[project]\nname = "splent_feature_alpha"\nversion = "1.0.0"\n\n'
            '[tool.splent.release]\nchannels = ["github"]\n'
        )
        result = _run(
            ["--feature", "alpha"],
            tags={"repo": ["v1.0.0"]},
            pypi={},
        )
        assert result.exit_code == 0
        assert "github only by declaration" in result.output

    def test_package_without_a_remote_is_skipped_not_failed(self, workspace):
        """Nothing to compare is fine and does not fail the run."""
        with patch.object(mod, "_repo_of", return_value=None):
            result = CliRunner(mix_stderr=False).invoke(
                mod.check_releases, ["--feature", "alpha"]
            )
        assert result.exit_code == 0
        assert "no origin remote" in result.output
        assert "could NOT be checked" not in result.output

    def test_rate_limited_github_is_not_reported_as_divergence(self, workspace):
        error = registry.RegistryError("boom", status=429, rate_limited=True)
        result = _run(["--feature", "alpha"], tags={"repo": error})
        assert "rate limiting" in result.output
        # Not a divergence, and not silence either.
        assert "tagged but not on PyPI" not in result.output
        assert "could NOT be checked" in result.output

    def test_unanswered_package_never_reads_as_in_sync(self, workspace):
        """A 401 must not produce a green headline. Unknown is not agreement."""
        error = registry.RegistryError("boom", status=401)
        result = _run(["--feature", "alpha"], tags={"repo": error})
        assert result.exit_code == 1
        assert "agree for all" not in result.output
        assert "Not knowing is not the same as being in sync" in result.output

    def test_divergent_only_hides_healthy_packages(self, workspace):
        result = _run(
            ["--divergent-only"],
            tags={"repo": ["v1.0.0"]},
            pypi={
                "splent_feature_alpha": ["1.0.0"],
                "splent_feature_beta": ["1.0.0"],
                "splent_cli": ["1.0.0"],
                "innosoft_app": ["1.0.0"],
            },
        )
        assert result.exit_code == 0
        assert "splent_feature_alpha" not in result.output

    def test_no_token_hint(self, workspace):
        result = _run(["--feature", "alpha"], tags={"repo": []}, pypi={})
        assert "GITHUB_TOKEN" in result.output

    def test_empty_workspace_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        result = CliRunner(mix_stderr=False).invoke(mod.check_releases, [])
        assert result.exit_code == 0
        assert "No releasable packages" in result.output
