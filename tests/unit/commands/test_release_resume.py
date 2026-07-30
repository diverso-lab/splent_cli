"""Tests for release:resume, the recovery path for a half-published version.

The properties that matter:

  1. it NEVER bumps the version, because a rerun of feature:release does, and
     that is how features reached v0.2.1 without ever reaching PyPI
  2. it never uploads artifacts that do not match the tag, and it gets there
     without moving the operator's checkout
  3. a channel that is blocked never stops a channel that is not. The command
     exists to repair a workspace where something is already broken, so
     refusing to do the reachable half would make it useless exactly when it
     is needed

Everything else is "do only what is missing". No network, no publishing.
"""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from splent_cli.commands.release import release_resume as mod
from splent_cli.commands.release.release_resume import release_resume, resolve_target
from splent_cli.services import release, release_gate


@pytest.fixture
def feature_ws(tmp_path, monkeypatch):
    """A workspace holding one editable feature at version 0.2.1."""
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    feature = tmp_path / "splent_feature_demo"
    feature.mkdir()
    (feature / "pyproject.toml").write_text(
        '[project]\nname = "splent_feature_demo"\nversion = "0.2.1"\n'
    )
    return tmp_path


@pytest.fixture
def snapshot_exists(feature_ws):
    """The read-only snapshot of v0.2.1 is already in the cache."""
    path = (
        feature_ws
        / ".splent_cache"
        / "features"
        / "splent_io"
        / "splent_feature_demo@v0.2.1"
    )
    path.mkdir(parents=True)
    return path


def _gate(
    *,
    tag_exists=True,
    release_exists=True,
    pypi_published=True,
    ok=True,
    github_ok=None,
    pypi_ok=None,
):
    return release_gate.GateReport(
        statuses=[
            release_gate.ChannelStatus(
                release_gate.GITHUB,
                ok=ok if github_ok is None else github_ok,
                summary="github",
                tag_exists=tag_exists,
                already_published=release_exists,
            ),
            release_gate.ChannelStatus(
                release_gate.PYPI,
                ok=ok if pypi_ok is None else pypi_ok,
                summary="pypi",
                already_published=pypi_published,
            ),
        ]
    )


def _run(args, report=None, *, clean=True, tag=True, at_tag=True, input_text="y\n"):
    report = report if report is not None else _gate()
    with (
        patch.object(
            release, "get_repo_from_path", return_value="org/splent_feature_demo"
        ),
        patch.object(release, "local_tag_exists", return_value=tag),
        patch.object(release, "git_is_clean", return_value=clean),
        patch.object(release, "head_is_at_tag", return_value=at_tag),
        patch.object(release_gate, "run_gate", return_value=report) as gate,
        patch.object(release, "create_github_release", return_value=None) as gh,
        patch.object(release, "build_and_upload_pypi") as pypi,
        patch.object(release, "create_and_push_tag") as make_tag,
        patch.object(release, "update_version") as bump,
        patch("splent_cli.commands.clear.clear_build.clean_build_artifacts"),
        patch(
            "splent_cli.commands.feature.feature_release.create_versioned_snapshot"
        ) as snapshot,
        patch("subprocess.run") as sub,
    ):
        sub.return_value.returncode = 0
        result = CliRunner(mix_stderr=False).invoke(
            release_resume, args, input=input_text
        )
    return result, {
        "gate": gate,
        "github_release": gh,
        "pypi": pypi,
        "create_tag": make_tag,
        "bump": bump,
        "snapshot": snapshot,
        "subprocess": sub,
    }


# ── Target resolution ─────────────────────────────────────────────────


class TestResolveTarget:
    def test_cli_alias(self, feature_ws):
        path, label, kind = resolve_target("cli", str(feature_ws))
        assert path.endswith("splent_cli")
        assert label == "splent_cli"
        assert kind == "cli"

    def test_framework_alias(self, feature_ws):
        path, label, kind = resolve_target("framework", str(feature_ws))
        assert path.endswith("splent_framework")
        assert kind == "framework"

    def test_product_alias(self, feature_ws, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "my_app")
        path, label, kind = resolve_target("product", str(feature_ws))
        assert path.endswith("my_app")
        assert label == "my_app"
        assert kind == "product"

    def test_feature_name(self, feature_ws):
        path, label, kind = resolve_target("splent_feature_demo", str(feature_ws))
        assert path.endswith("splent_feature_demo")
        assert label.endswith("splent_feature_demo")
        assert kind == "feature"


# ── It never bumps ────────────────────────────────────────────────────


class TestNeverBumps:
    def test_uses_the_committed_version_and_does_not_bump(self, feature_ws):
        result, mocks = _run(["splent_feature_demo"], _gate(pypi_published=False))
        assert result.exit_code == 0
        mocks["bump"].assert_not_called()
        # The version comes from pyproject.toml, untouched.
        assert mocks["gate"].call_args.kwargs["version"] == "0.2.1"
        assert "v0.2.1" in result.output

    def test_gate_is_told_this_is_a_resume(self, feature_ws):
        _, mocks = _run(["splent_feature_demo"], _gate(pypi_published=False))
        assert mocks["gate"].call_args.kwargs["resume"] is True


# ── It publishes only source the tag describes ────────────────────────


class TestWorkingTreeGuards:
    def test_dirty_tree_without_a_tag_is_refused(self, feature_ws):
        result, mocks = _run(["splent_feature_demo"], clean=False, tag=False)
        assert result.exit_code == 1
        assert "uncommitted changes" in result.output
        mocks["pypi"].assert_not_called()

    def test_head_away_from_the_tag_builds_from_a_worktree(self, feature_ws):
        """The old behavior told the operator to check the tag out.

        In a SPLENT workspace the feature directory is symlinked into the
        running product, so a detached checkout silently changes what the
        product serves, and nothing told the operator how to get back.
        """
        result, mocks = _run(
            ["splent_feature_demo"], _gate(pypi_published=False), at_tag=False
        )
        assert result.exit_code == 0
        assert "git checkout" not in result.output
        assert "temporary worktree" in result.output
        mocks["pypi"].assert_called_once()

    def test_missing_pyproject_is_refused(self, feature_ws):
        (feature_ws / "splent_feature_demo" / "pyproject.toml").unlink()
        result, _ = _run(["splent_feature_demo"])
        assert result.exit_code == 1

    def test_a_version_with_no_tag_at_all_builds_from_the_working_tree(
        self, feature_ws
    ):
        """After the reorder, a PyPI failure leaves a commit and no tag at all.

        There is nothing to check out in that state, and the clean-tree guard
        above is what makes the working tree safe to build.
        """
        result, mocks = _run(
            ["splent_feature_demo"],
            _gate(tag_exists=False, release_exists=False, pypi_published=False),
            tag=False,
            at_tag=False,
        )
        assert result.exit_code == 0
        mocks["pypi"].assert_called_once()
        assert mocks["pypi"].call_args.args[0].endswith("splent_feature_demo")

    def test_a_worktree_that_cannot_be_created_stops_the_upload(self, feature_ws):
        with pytest.raises(SystemExit):
            with (
                patch.object(release, "head_is_at_tag", return_value=False),
                patch.object(release, "local_tag_exists", return_value=True),
            ):
                with patch.object(mod.subprocess, "run") as run:
                    run.return_value.returncode = 1
                    run.return_value.stderr = "no such tag"
                    with mod.build_tree(str(feature_ws), "v0.2.1"):
                        pass


# ── It does only what is missing ──────────────────────────────────────


class TestDoesOnlyWhatIsMissing:
    def test_nothing_missing_does_nothing(self, feature_ws, snapshot_exists):
        result, mocks = _run(["splent_feature_demo"], _gate())
        assert result.exit_code == 0
        assert "Nothing to do" in result.output
        mocks["github_release"].assert_not_called()
        mocks["pypi"].assert_not_called()
        mocks["snapshot"].assert_not_called()

    def test_missing_feature_snapshot_is_created(self, feature_ws):
        """The original run died before the snapshot, so the cache entry is gone."""
        result, mocks = _run(["splent_feature_demo"], _gate())
        assert result.exit_code == 0
        assert "create the local snapshot" in result.output
        mocks["snapshot"].assert_called_once()

    def test_present_snapshot_is_left_alone(self, feature_ws, snapshot_exists):
        result, mocks = _run(["splent_feature_demo"], _gate(pypi_published=False))
        assert result.exit_code == 0
        mocks["snapshot"].assert_not_called()

    def test_missing_pypi_uploads_only(self, feature_ws):
        result, mocks = _run(["splent_feature_demo"], _gate(pypi_published=False))
        assert result.exit_code == 0
        mocks["pypi"].assert_called_once()
        mocks["github_release"].assert_not_called()
        assert "upload splent_feature_demo 0.2.1 to PyPI" in result.output

    def test_missing_github_release_creates_only_that(self, feature_ws):
        result, mocks = _run(["splent_feature_demo"], _gate(release_exists=False))
        assert result.exit_code == 0
        mocks["github_release"].assert_called_once()
        mocks["pypi"].assert_not_called()

    def test_unpushed_tag_is_pushed(self, feature_ws):
        result, mocks = _run(
            ["splent_feature_demo"], _gate(tag_exists=False, release_exists=False)
        )
        assert result.exit_code == 0
        pushed = [
            call
            for call in mocks["subprocess"].call_args_list
            if call.args and call.args[0][:3] == ["git", "push", "origin"]
        ]
        assert pushed, "the missing tag should have been pushed"

    def test_a_tag_that_does_not_exist_locally_is_created(self, feature_ws):
        """After the reorder a PyPI failure leaves a commit and no tag at all."""
        result, mocks = _run(
            ["splent_feature_demo"],
            _gate(tag_exists=False, release_exists=False),
            tag=False,
        )
        assert result.exit_code == 0
        mocks["create_tag"].assert_called_once()

    def test_declining_the_confirmation_publishes_nothing(self, feature_ws):
        result, mocks = _run(
            ["splent_feature_demo"], _gate(pypi_published=False), input_text="n\n"
        )
        assert result.exit_code != 0
        mocks["pypi"].assert_not_called()

    def test_yes_flag_skips_the_prompt(self, feature_ws):
        result, mocks = _run(
            ["splent_feature_demo", "--yes"], _gate(pypi_published=False), input_text=""
        )
        assert result.exit_code == 0
        mocks["pypi"].assert_called_once()

    def test_resume_target_is_passed_to_the_uploader(self, feature_ws):
        _, mocks = _run(["splent_feature_demo"], _gate(pypi_published=False))
        assert mocks["pypi"].call_args.kwargs["resume_target"] == "splent_feature_demo"


# ── One broken channel does not block the other ───────────────────────


class TestMarketplaceIndex:
    """A version finished here is still a new version.

    The index the marketplace serves lives in another repository and does not
    hear about a release. feature:release asks it to rebuild; resume did not,
    so a version that needed two runs stayed invisible in the marketplace
    until the next scheduled build. courses v0.1.2 was exactly that case.
    """

    def test_finishing_a_release_asks_the_index_to_rebuild(self, feature_ws):
        with patch("splent_cli.services.index_refresh.request_rebuild") as rebuild:
            result, _ = _run(["splent_feature_demo"], _gate(pypi_published=False))
        assert result.exit_code == 0
        rebuild.assert_called_once()

    def test_nothing_missing_does_not_ask(self, feature_ws, snapshot_exists):
        """Nothing was published, so there is nothing new to show."""
        with patch("splent_cli.services.index_refresh.request_rebuild") as rebuild:
            result, _ = _run(["splent_feature_demo"], _gate())
        assert result.exit_code == 0
        rebuild.assert_not_called()


class TestPartialRecovery:
    def test_a_blocked_github_does_not_stop_the_pypi_half(self, feature_ws):
        """The exact state of the incident.

        13 features were tagged with nothing on PyPI while GITHUB_TOKEN was
        401. Refusing the whole command made every one of them unrecoverable
        behind a token none of them needed.
        """
        report = _gate(github_ok=False, pypi_ok=True, pypi_published=False)
        report.github.summary = "blocked, GITHUB_TOKEN was rejected (HTTP 401)"
        result, mocks = _run(["splent_feature_demo"], report)

        mocks["pypi"].assert_called_once()
        mocks["github_release"].assert_not_called()
        # The unfinished half is still reported, and the run still fails.
        assert "Cannot be finished in this run" in result.output
        assert "still incomplete on github" in result.output
        assert result.exit_code == 1

    def test_a_blocked_pypi_does_not_stop_the_github_half(self, feature_ws):
        report = _gate(github_ok=True, pypi_ok=False, release_exists=False)
        report.pypi.summary = "blocked, PyPI is rate limiting this account (HTTP 429)"
        result, mocks = _run(["splent_feature_demo"], report)

        mocks["github_release"].assert_called_once()
        mocks["pypi"].assert_not_called()
        assert result.exit_code == 1

    def test_every_channel_blocked_publishes_nothing(self, feature_ws):
        result, mocks = _run(
            ["splent_feature_demo"], _gate(ok=False, pypi_published=False)
        )
        assert result.exit_code == 1
        mocks["pypi"].assert_not_called()
        mocks["github_release"].assert_not_called()
        assert "no channel can be reached" in result.output

    def test_no_github_flag_skips_the_github_channel_entirely(self, feature_ws):
        """The escape hatch exists on the commands that create divergence.

        It has to exist on the one that repairs it too, or a PyPI-only gap
        stays unrepairable while an unrelated token is broken.
        """
        result, mocks = _run(
            ["splent_feature_demo", "--no-github"], _gate(pypi_published=False)
        )
        assert result.exit_code == 0
        assert mocks["gate"].call_args.kwargs["channels"] == ("pypi",)
        mocks["pypi"].assert_called_once()

    def test_no_github_does_not_try_to_snapshot_a_tag_that_never_left(self, feature_ws):
        """The snapshot is a clone of the tag from GitHub."""
        report = release_gate.GateReport(
            statuses=[
                release_gate.ChannelStatus(release_gate.GITHUB, skipped=True),
                release_gate.ChannelStatus(release_gate.PYPI, ok=True),
            ]
        )
        result, mocks = _run(["splent_feature_demo", "--no-github"], report)
        assert result.exit_code == 0
        mocks["snapshot"].assert_not_called()
        assert "local snapshot" not in result.output

    def test_no_pypi_flag_skips_the_pypi_channel_entirely(self, feature_ws):
        result, mocks = _run(
            ["splent_feature_demo", "--no-pypi"], _gate(release_exists=False)
        )
        assert result.exit_code == 0
        assert mocks["gate"].call_args.kwargs["channels"] == ("github",)
        mocks["pypi"].assert_not_called()

    def test_a_failed_github_release_is_reported_after_pypi_ran(self, feature_ws):
        """A GitHub release that keeps failing must not block the PyPI half forever."""
        report = _gate(release_exists=False, pypi_published=False)
        with (
            patch.object(
                release, "get_repo_from_path", return_value="org/splent_feature_demo"
            ),
            patch.object(release, "local_tag_exists", return_value=True),
            patch.object(release, "git_is_clean", return_value=True),
            patch.object(release, "head_is_at_tag", return_value=True),
            patch.object(release_gate, "run_gate", return_value=report),
            patch.object(
                release, "create_github_release", return_value="HTTP 403"
            ) as gh,
            patch.object(release, "build_and_upload_pypi") as pypi,
            patch("splent_cli.commands.clear.clear_build.clean_build_artifacts"),
            patch(
                "splent_cli.commands.feature.feature_release.create_versioned_snapshot"
            ),
            patch("subprocess.run"),
        ):
            result = CliRunner(mix_stderr=False).invoke(
                release_resume, ["splent_feature_demo", "--yes"]
            )

        assert result.exit_code == 1
        pypi.assert_called_once()
        gh.assert_called_once()
        assert "403" in result.output


# ── Docker, the third channel of a product ────────────────────────────


class TestProductDockerChannel:
    def test_a_product_resume_covers_the_image(self, feature_ws, monkeypatch):
        """The image is pushed last, so it is the likeliest thing left behind."""
        monkeypatch.setenv("SPLENT_APP", "my_app")
        monkeypatch.setenv("DOCKERHUB_USERNAME", "me")
        product = feature_ws / "my_app"
        product.mkdir()
        (product / "pyproject.toml").write_text(
            '[project]\nname = "my_app"\nversion = "1.0.0"\n'
        )

        report = release_gate.GateReport(
            statuses=[
                release_gate.ChannelStatus(
                    release_gate.GITHUB,
                    ok=True,
                    tag_exists=True,
                    already_published=True,
                ),
                release_gate.ChannelStatus(
                    release_gate.PYPI, ok=True, already_published=True
                ),
                release_gate.ChannelStatus(
                    release_gate.DOCKER, ok=True, already_published=False
                ),
            ]
        )
        with (
            patch.object(release, "get_repo_from_path", return_value="org/my_app"),
            patch.object(release, "local_tag_exists", return_value=True),
            patch.object(release, "git_is_clean", return_value=True),
            patch.object(release, "head_is_at_tag", return_value=True),
            patch.object(release_gate, "run_gate", return_value=report) as gate,
            patch(
                "splent_cli.commands.product.product_release.release_docker_image"
            ) as docker,
            patch("subprocess.run"),
        ):
            result = CliRunner(mix_stderr=False).invoke(
                release_resume, ["product", "--yes"]
            )

        assert result.exit_code == 0
        assert release_gate.DOCKER in gate.call_args.kwargs["channels"]
        docker.assert_called_once()
        assert "Docker image" in result.output
