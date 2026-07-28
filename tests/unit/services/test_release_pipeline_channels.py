"""Tests for the two-channel contract of the release pipeline.

What the pipeline must guarantee, and what these tests pin down:

  * the channel gate runs BEFORE the first mutation, so a refusal leaves the
    repository, the tag namespace and both remotes untouched
  * the gate is asked again immediately before the first mutation, so the
    verdict acted on is not the one from before the test run
  * PyPI is published BEFORE anything is pushed to GitHub, because a PyPI
    version can never be replaced and an unpushed tag costs nothing
  * a GitHub release that cannot be created is a failure, not a note, and it
    does not take the channels that already succeeded down with it
  * turning a channel off means nothing at all is published there, and that is
    explicit, loud, and visible in the result

No test here touches the network or publishes anything.
"""

import subprocess
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from splent_cli.services import registry, release, release_gate


def _gate(github_ok=True, pypi_ok=True, **kw):
    return release_gate.GateReport(
        statuses=[
            release_gate.ChannelStatus(
                release_gate.GITHUB, ok=github_ok, summary="github", **kw
            ),
            release_gate.ChannelStatus(release_gate.PYPI, ok=pypi_ok, summary="pypi"),
        ]
    )


def _pyproject(tmp_path, extra=""):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "splent_feature_demo"\nversion = "0.1.0"\n' + extra
    )
    return tmp_path


# ── The gate runs before anything irreversible ────────────────────────


class TestChannelGateOrdering:
    def _run(self, tmp_path, report, input_text="", **kwargs):
        """Run the pipeline with every side effect mocked, return the mocks."""
        order = []

        def _record(name, result=None):
            def _fn(*a, **k):
                order.append(name)
                return result

            return _fn

        with (
            patch.object(release, "validate_release_env"),
            patch.object(release, "get_repo_from_path", return_value="org/repo"),
            patch.object(release_gate, "run_gate", return_value=report) as gate,
            patch.object(release, "run_pre_release_checks") as checks,
            patch.object(release, "update_version") as upd,
            patch.object(release, "commit_locally") as commit,
            patch.object(release, "push_main", side_effect=_record("push")) as push,
            patch.object(
                release, "create_and_push_tag", side_effect=_record("tag")
            ) as tag,
            patch.object(
                release, "create_github_release", side_effect=_record("release")
            ) as gh_release,
            patch.object(
                release, "build_and_upload_pypi", side_effect=_record("pypi")
            ) as pypi,
            patch("splent_cli.commands.clear.clear_build.clean_build_artifacts"),
        ):
            mocks = {
                "gate": gate,
                "checks": checks,
                "update_version": upd,
                "commit": commit,
                "push": push,
                "tag": tag,
                "github_release": gh_release,
                "pypi": pypi,
                "order": order,
            }

            @click.command()
            def cmd():
                release.run_release_pipeline(
                    "demo", str(tmp_path), "1.0.0", kind="feature", **kwargs
                )

            result = CliRunner(mix_stderr=False).invoke(cmd, input=input_text)
        return result, mocks

    def test_blocked_channel_stops_before_the_first_mutation(self, tmp_path):
        _pyproject(tmp_path)
        result, mocks = self._run(tmp_path, _gate(pypi_ok=False))

        assert result.exit_code == 1
        # Nothing irreversible happened.
        mocks["update_version"].assert_not_called()
        mocks["commit"].assert_not_called()
        mocks["push"].assert_not_called()
        mocks["tag"].assert_not_called()
        mocks["github_release"].assert_not_called()
        mocks["pypi"].assert_not_called()
        assert "Nothing was changed" in result.output

    def test_blocked_channel_stops_before_lint_and_tests(self, tmp_path):
        """The cheap network check comes first, so a blocked channel is fast."""
        _pyproject(tmp_path)
        _, mocks = self._run(tmp_path, _gate(github_ok=False))
        mocks["checks"].assert_not_called()

    def test_the_gate_is_asked_again_after_the_tests(self, tmp_path):
        """A verdict from before a long test run is not a verdict any more.

        The first pass is cheap and fails fast; the binding one runs seconds
        before the first mutation, and it is the one that names the project to
        PyPI.
        """
        _pyproject(tmp_path)
        _, mocks = self._run(tmp_path, _gate())
        assert mocks["gate"].call_count == 2
        assert mocks["gate"].call_args_list[0].kwargs["deep"] is False
        assert mocks["gate"].call_args_list[1].kwargs["deep"] is True

    def test_a_channel_that_goes_down_during_the_tests_is_caught(self, tmp_path):
        """The failure the second pass exists for."""
        _pyproject(tmp_path)
        reports = [_gate(), _gate(pypi_ok=False)]
        with (
            patch.object(release, "validate_release_env"),
            patch.object(release, "get_repo_from_path", return_value="org/repo"),
            patch.object(release_gate, "run_gate", side_effect=reports),
            patch.object(release, "run_pre_release_checks"),
            patch.object(release, "update_version") as upd,
            patch.object(release, "build_and_upload_pypi") as pypi,
        ):

            @click.command()
            def cmd():
                release.run_release_pipeline(
                    "demo", str(tmp_path), "1.0.0", kind="feature"
                )

            result = CliRunner(mix_stderr=False).invoke(cmd)
        assert result.exit_code == 1
        upd.assert_not_called()
        pypi.assert_not_called()

    def test_rate_limited_refusal_says_rate_limited(self, tmp_path):
        _pyproject(tmp_path)
        report = release_gate.GateReport(
            statuses=[
                release_gate.ChannelStatus(release_gate.GITHUB, ok=True),
                release_gate.ChannelStatus(
                    release_gate.PYPI,
                    ok=False,
                    rate_limited=True,
                    summary="blocked, PyPI is rate limiting this account (HTTP 429)",
                ),
            ]
        )
        result, _ = self._run(tmp_path, report)
        assert "RATE LIMITED" in result.output
        assert "429" in result.output

    def test_passing_gate_runs_the_whole_pipeline(self, tmp_path):
        _pyproject(tmp_path)
        result, mocks = self._run(tmp_path, _gate())
        assert result.exit_code == 0
        mocks["update_version"].assert_called_once()
        mocks["commit"].assert_called_once()
        mocks["push"].assert_called_once()
        mocks["tag"].assert_called_once()
        mocks["github_release"].assert_called_once()
        mocks["pypi"].assert_called_once()

    def test_pypi_is_published_before_anything_is_pushed(self, tmp_path):
        """The whole point of the ordering.

        A PyPI version can never be replaced or deleted; a tag that was never
        pushed costs nothing. Uploading first means a PyPI refusal, including
        the rate limit on creating a new project, leaves nothing published
        anywhere and no divergence to repair.
        """
        _pyproject(tmp_path)
        _, mocks = self._run(tmp_path, _gate())
        assert mocks["order"] == ["pypi", "push", "tag", "release"]

    def test_a_pypi_failure_leaves_nothing_pushed(self, tmp_path):
        _pyproject(tmp_path)
        with (
            patch.object(release, "validate_release_env"),
            patch.object(release, "get_repo_from_path", return_value="org/repo"),
            patch.object(release_gate, "run_gate", return_value=_gate()),
            patch.object(release, "run_pre_release_checks"),
            patch.object(release, "update_version"),
            patch.object(release, "commit_locally"),
            patch.object(release, "push_main") as push,
            patch.object(release, "create_and_push_tag") as tag,
            patch.object(release, "create_github_release") as gh_release,
            patch.object(
                release, "build_and_upload_pypi", side_effect=SystemExit(1)
            ),
            patch("splent_cli.commands.clear.clear_build.clean_build_artifacts"),
        ):

            @click.command()
            def cmd():
                release.run_release_pipeline(
                    "demo", str(tmp_path), "1.0.0", kind="feature"
                )

            result = CliRunner(mix_stderr=False).invoke(cmd)

        assert result.exit_code == 1
        push.assert_not_called()
        tag.assert_not_called()
        gh_release.assert_not_called()

    def test_gate_is_asked_about_the_distribution_name(self, tmp_path):
        """PyPI knows the package by [project].name, not by the directory."""
        _pyproject(tmp_path)
        _, mocks = self._run(tmp_path, _gate())
        assert mocks["gate"].call_args.kwargs["package"] == "splent_feature_demo"
        assert mocks["gate"].call_args.kwargs["repo"] == "org/repo"
        assert mocks["gate"].call_args.kwargs["version"] == "1.0.0"

    def test_no_pypi_skips_only_the_upload(self, tmp_path):
        _pyproject(tmp_path)
        result, mocks = self._run(tmp_path, _gate(), channels=(release_gate.GITHUB,))
        assert result.exit_code == 0
        mocks["pypi"].assert_not_called()
        mocks["github_release"].assert_called_once()
        # The GitHub release is told the package is not on PyPI, so the
        # published notes say so instead of advertising a pip install.
        assert mocks["github_release"].call_args.kwargs["on_pypi"] is False
        # The asymmetry is stated in the result, not left implicit.
        assert "SKIPPED on purpose" in result.output
        assert "do not match" in result.output

    def test_no_github_pushes_nothing_at_all(self, tmp_path):
        """The flag turns off the GitHub channel, not only the release object.

        A commit push and a tag push are publications to GitHub. Leaving them
        running while the gate was told to skip GitHub meant pushing to a
        repository nothing had verified, and guaranteed a tag with no release.
        """
        _pyproject(tmp_path)
        result, mocks = self._run(tmp_path, _gate(), channels=(release_gate.PYPI,))
        assert result.exit_code == 0
        mocks["github_release"].assert_not_called()
        mocks["push"].assert_not_called()
        mocks["pypi"].assert_called_once()
        # The tag is created locally and explicitly not pushed.
        assert mocks["tag"].call_args.kwargs["push"] is False
        assert "local only" in result.output

    def test_no_channel_at_all_is_refused(self, tmp_path):
        _pyproject(tmp_path)
        result, mocks = self._run(tmp_path, _gate(), channels=())
        assert result.exit_code == 1
        assert "publish nowhere" in result.output
        mocks["gate"].assert_not_called()

    def test_an_unproven_channel_does_not_stop_to_ask(self, tmp_path):
        """Nothing can diverge from a PyPI refusal now, so there is nothing to ask.

        The prompt used to be the only thing standing between an unproven
        new-project upload and a pushed tag. The ordering replaced it with a
        guarantee.
        """
        _pyproject(tmp_path)
        report = _gate()
        report.pypi.unproven = True
        report.pypi.new_project = True

        result, mocks = self._run(tmp_path, report)
        assert result.exit_code == 0
        assert "could not be proven" in result.output
        assert "nothing will have diverged" in result.output
        mocks["pypi"].assert_called_once()

    def test_declared_channels_are_honored_without_a_flag(self, tmp_path):
        _pyproject(tmp_path, extra='\n[tool.splent.release]\nchannels = ["github"]\n')
        result, mocks = self._run(tmp_path, _gate())
        assert result.exit_code == 0
        mocks["pypi"].assert_not_called()
        assert mocks["gate"].call_args.kwargs["channels"] == ("github",)

    def test_a_failed_github_release_does_not_undo_the_rest(self, tmp_path):
        """Requirement 4 without turning one gap into two.

        The release object is the last thing created, so its failure is
        reported as a divergence and exits non zero, but PyPI has already been
        published and the snapshot hook still runs.
        """
        _pyproject(tmp_path)
        hook = MagicMock()
        with (
            patch.object(release, "validate_release_env"),
            patch.object(release, "get_repo_from_path", return_value="org/repo"),
            patch.object(release_gate, "run_gate", return_value=_gate()),
            patch.object(release, "run_pre_release_checks"),
            patch.object(release, "update_version"),
            patch.object(release, "commit_locally"),
            patch.object(release, "push_main"),
            patch.object(release, "create_and_push_tag"),
            patch.object(
                release, "create_github_release", return_value="HTTP 403"
            ),
            patch.object(release, "build_and_upload_pypi") as pypi,
            patch("splent_cli.commands.clear.clear_build.clean_build_artifacts"),
        ):

            @click.command()
            def cmd():
                release.run_release_pipeline(
                    "demo",
                    str(tmp_path),
                    "1.0.0",
                    kind="feature",
                    resume_target="splent_feature_demo",
                    post_pypi_hook=hook,
                )

            result = CliRunner(mix_stderr=False).invoke(cmd)

        assert result.exit_code == 1
        pypi.assert_called_once()
        hook.assert_called_once()
        assert "DIVERGED" in result.output
        assert "splent release:resume splent_feature_demo" in result.output


    def test_a_local_only_tag_is_reported_as_such(self, tmp_path):
        _pyproject(tmp_path)
        result, _ = self._run(tmp_path, _gate(), channels=(release_gate.PYPI,))
        assert "The commit and the tag" in result.output
        assert "local only" in result.output


# ── resolve_channels ──────────────────────────────────────────────────


class TestResolveChannels:
    def test_defaults_to_both(self, tmp_path):
        _pyproject(tmp_path)
        assert release.resolve_channels(str(tmp_path)) == ("github", "pypi")

    def test_no_pypi_removes_pypi(self, tmp_path):
        _pyproject(tmp_path)
        assert release.resolve_channels(str(tmp_path), no_pypi=True) == ("github",)

    def test_no_github_removes_github(self, tmp_path):
        _pyproject(tmp_path)
        assert release.resolve_channels(str(tmp_path), no_github=True) == ("pypi",)

    def test_declaration_and_flag_combine(self, tmp_path):
        _pyproject(tmp_path, extra='\n[tool.splent.release]\nchannels = ["github"]\n')
        assert release.resolve_channels(str(tmp_path), no_github=True) == ()

    def test_extra_channels_are_added(self, tmp_path):
        _pyproject(tmp_path)
        assert release.resolve_channels(
            str(tmp_path), extra=(release_gate.DOCKER,)
        ) == ("github", "pypi", "docker")

    def test_an_unreadable_declaration_refuses_instead_of_guessing(self, tmp_path):
        """Guessing "both" would publish a private package to PyPI, permanently."""
        _pyproject(tmp_path, extra='\n[tool.splent.release]\nchannels = ["githb"]\n')

        @click.command()
        def cmd():
            release.resolve_channels(str(tmp_path))

        result = CliRunner(mix_stderr=False).invoke(cmd)
        assert result.exit_code == 1
        assert "githb" in result.output


# ── The escape hatch is explicit and loud ─────────────────────────────


class TestConfirmSingleChannel:
    def _invoke(self, input_text, **kwargs):
        @click.command()
        def cmd():
            release.confirm_single_channel(("github",), **kwargs)

        return CliRunner(mix_stderr=False).invoke(cmd, input=input_text)

    def test_no_flags_asks_nothing(self):
        @click.command()
        def cmd():
            release.confirm_single_channel(("github", "pypi"))
            click.echo("done")

        result = CliRunner(mix_stderr=False).invoke(cmd)
        assert result.exit_code == 0
        assert "done" in result.output

    def test_flag_warns_and_asks(self):
        result = self._invoke("y\n", no_pypi=True)
        assert "ONLY" in result.output
        assert "PyPI is turned off" in result.output
        assert result.exit_code == 0

    def test_no_github_says_the_commit_and_tag_stay_local(self):
        result = self._invoke("y\n", no_github=True)
        assert "stay on this machine" in result.output

    def test_refusing_aborts(self):
        result = self._invoke("n\n", no_pypi=True)
        assert result.exit_code != 0

    def test_default_is_no(self):
        """Enter on the prompt must not publish half a release."""
        result = self._invoke("\n", no_pypi=True)
        assert result.exit_code != 0


# ── A failed GitHub release is a failure ──────────────────────────────


class TestCreateGitHubReleaseNeverSwallows:
    def _invoke(self, result_obj=None, error=None, token="tok", **kwargs):
        @click.command()
        def cmd():
            release.create_github_release("org/repo", "v1.2.3", token, **kwargs)

        target = patch.object(
            registry,
            "github_create_release",
            side_effect=error if error else None,
            return_value=result_obj,
        )
        with target:
            return CliRunner(mix_stderr=False).invoke(cmd)

    def test_success_is_quiet(self):
        result = self._invoke(
            registry.ReleaseResult(status=201, created=True, url="https://gh/r/1")
        )
        assert result.exit_code == 0
        assert "release created" in result.output

    def test_already_exists_is_not_a_failure(self):
        result = self._invoke(registry.ReleaseResult(status=422, already_exists=True))
        assert result.exit_code == 0

    def test_401_aborts_and_names_the_command_that_finishes_everything(self):
        """The old behavior printed 'skipped (401)' and carried on.

        The recovery offered is release:resume and not a bare `gh release
        create`, which would make the release page green while leaving whatever
        else is missing unpublished.
        """
        result = self._invoke(
            registry.ReleaseResult(status=401), resume_target="splent_feature_demo"
        )
        assert result.exit_code == 1
        assert "diverged" in result.output.lower()
        assert "splent release:resume splent_feature_demo" in result.output
        assert "gh release create" not in result.output

    def test_403_aborts(self):
        result = self._invoke(registry.ReleaseResult(status=403))
        assert result.exit_code == 1

    def test_429_is_reported_as_a_rate_limit(self):
        result = self._invoke(registry.ReleaseResult(status=429, rate_limited=True))
        assert result.exit_code == 1
        assert "rate limiting" in result.output
        assert "not a credential problem" in result.output

    def test_unreachable_github_aborts(self):
        result = self._invoke(error=registry.RegistryError("Network error: down"))
        assert result.exit_code == 1
        assert "diverged" in result.output.lower()

    def test_missing_token_aborts(self):
        result = self._invoke(
            registry.ReleaseResult(status=201, created=True), token=None
        )
        assert result.exit_code == 1
        assert "GITHUB_TOKEN" in result.output

    def test_non_fatal_mode_reports_instead_of_exiting(self):
        """So the caller can finish the channels that can still be finished."""
        reason = {}

        @click.command()
        def cmd():
            reason["value"] = release.create_github_release(
                "org/repo", "v1.2.3", "tok", fatal=False
            )

        with patch.object(
            registry,
            "github_create_release",
            return_value=registry.ReleaseResult(status=403),
        ):
            result = CliRunner(mix_stderr=False).invoke(cmd)

        assert result.exit_code == 0
        assert "403" in reason["value"]


class TestReleaseBody:
    def test_pypi_release_advertises_pip_install(self):
        body = release.release_body("org/splent_feature_demo", "v1.0.0")
        assert "pip install splent_feature_demo==1.0.0" in body

    def test_github_only_release_says_it_is_not_on_pypi(self):
        body = release.release_body("org/splent_feature_demo", "v1.0.0", on_pypi=False)
        assert "not published on PyPI" in body
        assert "pip install git+" in body


# ── A failed PyPI upload names the divergence and the fix ─────────────


class TestUploadFailureReporting:
    def _invoke(self, stderr, **kwargs):
        dist = MagicMock()
        error = subprocess.CalledProcessError(1, "twine upload")
        error.stderr = stderr

        @click.command()
        def cmd():
            release.build_and_upload_pypi(
                "/tmp/pkg", resume_target="splent_feature_demo", **kwargs
            )

        with (
            patch.object(release.subprocess, "run", side_effect=[dist, dist, error]),
            patch("glob.glob", return_value=["/tmp/pkg/dist/x.whl"]),
        ):
            return CliRunner(mix_stderr=False).invoke(cmd)

    def test_429_is_reported_as_a_rate_limit(self):
        result = self._invoke("HTTPError: 429 Too Many Requests from upload.pypi.org")
        assert result.exit_code == 1
        assert "rate limited" in result.output.lower()
        assert "not a credential problem" in result.output

    def test_failure_names_the_divergence_and_the_recovery(self):
        result = self._invoke("HTTPError: 400 Bad Request")
        assert result.exit_code == 1
        assert "DIVERGED" in result.output
        assert "splent release:resume splent_feature_demo" in result.output

    def test_in_the_pipeline_a_failure_is_not_a_divergence(self):
        """Nothing has been pushed at that point, so the report must not say it has."""
        result = self._invoke("HTTPError: 429 Too Many Requests", diverged=False)
        assert result.exit_code == 1
        assert "did NOT diverge" in result.output
        assert "splent release:resume splent_feature_demo" in result.output

    def test_credential_failure_is_not_called_a_rate_limit(self):
        result = self._invoke("HTTPError: 403 Forbidden")
        assert "rate limited" not in result.output.lower()
        assert "not a rate limit" in result.output

    def test_no_stderr_still_reports_cleanly(self):
        result = self._invoke(None)
        assert result.exit_code == 1
        assert "Traceback" not in result.output


class TestTwineEnvironment:
    def test_the_upload_runs_with_the_credentials_the_gate_checked(self, monkeypatch):
        """twine reads TWINE_* and nothing else.

        Accepting PYPI_* everywhere except in the process that actually uploads
        meant the gate could pass on one credential and the upload run with none.
        """
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.setenv("PYPI_USERNAME", "__token__")
        monkeypatch.setenv("PYPI_PASSWORD", "pypi-secret")
        env = release.twine_env()
        assert env["TWINE_USERNAME"] == "__token__"
        assert env["TWINE_PASSWORD"] == "pypi-secret"

    def test_the_upload_is_given_that_environment(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.setenv("PYPI_USERNAME", "__token__")
        monkeypatch.setenv("PYPI_PASSWORD", "pypi-secret")
        calls = []

        def _run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return MagicMock()

        @click.command()
        def cmd():
            release.build_and_upload_pypi(str(tmp_path))

        with (
            patch.object(release.subprocess, "run", side_effect=_run),
            patch("glob.glob", return_value=[str(tmp_path / "dist" / "x.whl")]),
        ):
            CliRunner(mix_stderr=False).invoke(cmd)

        upload = [c for c in calls if "twine" in c[0]][0]
        assert upload[1]["env"]["TWINE_USERNAME"] == "__token__"
        assert upload[1]["env"]["TWINE_PASSWORD"] == "pypi-secret"


class TestLooksRateLimited:
    @pytest.mark.parametrize(
        "text", ["429 Too Many Requests", "HTTP 429", "too many requests"]
    )
    def test_positive(self, text):
        assert release._looks_rate_limited(text) is True

    @pytest.mark.parametrize("text", ["403 Forbidden", "", None, "400 Bad Request"])
    def test_negative(self, text):
        assert release._looks_rate_limited(text) is False


# ── Version numbers are not burned on a rerun ─────────────────────────


class TestGuardIncompleteRelease:
    def test_unfinished_release_blocks_a_new_version(self):
        incomplete = release_gate.IncompleteRelease(tag="v0.2.1", missing=["PyPI"])

        @click.command()
        def cmd():
            release.guard_incomplete_release(
                "org", "repo", "pkg", resume_target="splent_feature_team"
            )

        with patch.object(
            release_gate, "find_incomplete_release", return_value=incomplete
        ):
            result = CliRunner(mix_stderr=False).invoke(cmd)

        assert result.exit_code == 1
        assert "v0.2.1" in result.output
        assert "burn a version number" in result.output
        assert "splent release:resume splent_feature_team" in result.output

    def test_complete_release_does_not_block(self):
        @click.command()
        def cmd():
            release.guard_incomplete_release("org", "repo", "pkg")
            click.echo("proceeding")

        with patch.object(release_gate, "find_incomplete_release", return_value=None):
            result = CliRunner(mix_stderr=False).invoke(cmd)

        assert result.exit_code == 0
        assert "proceeding" in result.output

    def test_a_failed_lookup_is_reported_and_left_to_the_gate(self):
        """The early copy is advisory. It must not pretend it checked."""

        @click.command()
        def cmd():
            release.guard_incomplete_release("org", "repo", "pkg")
            click.echo("proceeding")

        with patch.object(
            release_gate,
            "find_incomplete_release",
            side_effect=registry.RegistryError("boom", status=401),
        ):
            result = CliRunner(mix_stderr=False).invoke(cmd)

        assert result.exit_code == 0
        assert "could not check" in result.output
        assert "proceeding" in result.output

    def test_wizard_checks_only_when_a_package_is_known(self):
        with (
            patch.object(release, "fetch_latest_tag", return_value="v1.0.0"),
            patch.object(release, "guard_incomplete_release") as guard,
            patch("click.prompt", return_value="4"),
        ):
            with pytest.raises(SystemExit):
                release.semver_wizard("org", "repo")
            guard.assert_not_called()

    def test_wizard_guards_when_a_package_is_given(self):
        with (
            patch.object(release, "fetch_latest_tag", return_value="v1.0.0"),
            patch.object(release, "guard_incomplete_release") as guard,
            patch("click.prompt", return_value="4"),
        ):
            with pytest.raises(SystemExit):
                release.semver_wizard("org", "repo", package="pkg")
            guard.assert_called_once()


# ── The wizard never invents a current version ────────────────────────


class TestSemverWizardSources:
    def _run(self, tmp_path, remote, local, declared, input_text="4\n"):
        (tmp_path / "pyproject.toml").write_text(
            f'[project]\nname = "pkg"\nversion = "{declared}"\n'
        )

        @click.command()
        def cmd():
            release.semver_wizard("org", "repo", path=str(tmp_path))

        with (
            patch.object(release, "fetch_latest_tag", **remote),
            patch.object(release, "local_latest_tag", return_value=local),
        ):
            return CliRunner(mix_stderr=False).invoke(cmd, input=input_text)

    def test_a_failed_tag_lookup_is_never_called_no_tags(self, tmp_path):
        """Reproduces the live failure: 401 on /tags offered v0.0.1 for a v0.2.0 repo."""
        result = self._run(
            tmp_path,
            remote={"side_effect": registry.RegistryError("boom", status=401)},
            local="v0.2.0",
            declared="0.2.0",
        )
        assert "No tags found" not in result.output
        assert "would not list the tags" in result.output
        assert "v0.2.0" in result.output
        # The patch offer is above what exists, never below it.
        assert "v0.2.1" in result.output
        assert "v0.0.1" not in result.output

    def test_the_highest_source_wins(self, tmp_path):
        result = self._run(
            tmp_path, remote={"return_value": "v1.0.0"}, local="v1.2.0", declared="1.1.0"
        )
        assert "Current version: v1.2.0" in result.output

    def test_nothing_known_anywhere_refuses_rather_than_guessing(self, tmp_path):
        result = self._run(
            tmp_path,
            remote={"side_effect": registry.RegistryError("boom", status=500)},
            local=None,
            declared="",
        )
        assert result.exit_code == 1
        assert "Refusing to guess" in result.output

    def test_a_genuinely_new_package_still_starts_at_zero(self, tmp_path):
        result = self._run(
            tmp_path, remote={"return_value": None}, local=None, declared=""
        )
        assert "first release" in result.output
        assert "v0.0.1" in result.output


class TestHighestKnownVersion:
    def test_prefers_the_highest(self):
        assert release.highest_known_version("v1.0.0", "v1.2.0", "1.1.0") == "v1.2.0"

    def test_ignores_missing_sources(self):
        assert release.highest_known_version(None, None, "1.1.0") == "1.1.0"

    def test_nothing_is_none(self):
        assert release.highest_known_version(None, None, None) is None

    def test_unparsable_values_are_ignored(self):
        assert release.highest_known_version("nightly", None, "1.0.0") == "1.0.0"


# ── Small readers ─────────────────────────────────────────────────────


class TestProjectReaders:
    def test_reads_name_and_version(self, tmp_path):
        p = _pyproject(tmp_path) / "pyproject.toml"
        assert release.read_project_name(str(p)) == "splent_feature_demo"
        assert release.read_project_version(str(p)) == "0.1.0"

    def test_missing_file_is_none(self, tmp_path):
        p = str(tmp_path / "nope.toml")
        assert release.read_project_name(p) is None
        assert release.read_project_version(p) is None

    def test_broken_toml_is_none(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text("[project\nname =")
        assert release.read_project_name(str(p)) is None
