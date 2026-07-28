"""Tests for the channel gate in services/release_gate.py.

The gate is the thing that must make a silent divergence impossible, so these
tests are about the failure modes that caused the damage:

  1. reporting "fine" when a channel would refuse   (false green, permanent)
  2. reporting a 429 as a credential problem, or a 401 as a rate limit
  3. proving only what a nameless probe can prove, which is not the failure
     that actually happened (creating a NEW project on PyPI)

Every network call is mocked at services/registry.py, the single boundary.
Nothing here reaches GitHub or PyPI.
"""

import time

import pytest

from splent_cli.services import registry, release_gate


# ── Helpers ───────────────────────────────────────────────────────────


def _probe(status, *, rate_limited=False, retry_after=None, named=False):
    return registry.PyPIProbe(
        status=status, rate_limited=rate_limited, retry_after=retry_after, named=named
    )


def _repo(*, archived=False, push=True):
    return {"archived": archived, "permissions": {"push": push, "pull": True}}


@pytest.fixture
def gh(monkeypatch):
    """A healthy GitHub, overridable per test."""
    state = {
        "rate_limit": {"limit": 5000, "remaining": 4999, "reset": 0},
        "user": {"login": "owner"},
        "repo": _repo(),
        "tag_exists": False,
        "release": {"id": 1},
        "latest_tag": None,
    }

    def _value(key):
        value = state[key]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(
        registry, "github_rate_limit", lambda token=None: _value("rate_limit")
    )
    monkeypatch.setattr(registry, "github_user", lambda token: _value("user"))
    monkeypatch.setattr(
        registry, "fetch_repo", lambda org, name, token=None: _value("repo")
    )
    monkeypatch.setattr(
        registry,
        "github_tag_exists",
        lambda org, name, tag, token=None: _value("tag_exists"),
    )
    monkeypatch.setattr(
        registry,
        "github_release_by_tag",
        lambda org, name, tag, token=None: _value("release"),
    )
    monkeypatch.setattr(
        registry,
        "latest_semver_tag",
        lambda org, name, token=None, strict=False: _value("latest_tag"),
    )
    return state


@pytest.fixture
def pypi(monkeypatch):
    """A healthy PyPI, overridable per test."""
    state = {"probe": None, "project_exists": True, "version_exists": False}

    def _upload_probe(user, password, *, package=None, version=None, **kw):
        value = state["probe"]
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        return _probe(400, named=bool(package))

    def _project_exists(package, timeout=10):
        value = state["project_exists"]
        if isinstance(value, Exception):
            raise value
        return value

    def _version_exists(package, version, strict=False):
        value = state["version_exists"]
        if isinstance(value, dict):
            value = value.get(version, False)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(registry, "pypi_upload_probe", _upload_probe)
    monkeypatch.setattr(registry, "pypi_project_exists", _project_exists)
    monkeypatch.setattr(registry, "pypi_version_exists", _version_exists)
    return state


def _text(status) -> str:
    return (status.summary + " " + " ".join(status.hints)).lower()


# ── GitHub channel ────────────────────────────────────────────────────


class TestCheckGitHub:
    def test_healthy_repo_is_ready(self, gh):
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.ok is True
        assert st.blocked is False
        assert "org/repo" in st.summary

    def test_missing_token_is_a_refusal_not_a_warning(self, gh):
        st = release_gate.check_github("org/repo", "v1.0.0", token=None)
        assert st.blocked is True
        assert "github_token" in _text(st)
        # The escape hatch is offered, never taken silently.
        assert "--no-github" in _text(st)

    def test_401_is_credentials_never_rate_limit(self, gh, monkeypatch):
        def _raise(token):
            raise registry.RegistryError("boom", status=401)

        monkeypatch.setattr(registry, "github_user", _raise)
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.blocked is True
        assert st.rate_limited is False
        assert "401" in st.summary
        assert "rate limit" not in _text(st)

    def test_exhausted_budget_is_a_rate_limit_with_a_real_window(self, gh):
        reset = int(time.time()) + 600
        gh["rate_limit"] = {"limit": 5000, "remaining": 0, "reset": reset}
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.blocked is True
        assert st.rate_limited is True
        # The window comes from GitHub's own reset header, not from a guess.
        assert "resets at" in _text(st)
        assert "not a credential problem" in _text(st)

    def test_429_is_a_rate_limit_not_a_credential_problem(self, gh, monkeypatch):
        def _raise(token):
            raise registry.RegistryError("boom", status=429, rate_limited=True)

        monkeypatch.setattr(registry, "github_user", _raise)
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.rate_limited is True
        assert "429" in st.summary
        assert "invalid" not in _text(st)

    def test_403_with_exhausted_header_is_a_rate_limit(self, gh, monkeypatch):
        def _raise(token):
            raise registry.RegistryError("boom", status=403, rate_limited=True)

        monkeypatch.setattr(registry, "github_user", _raise)
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.rate_limited is True

    def test_unknown_repo_is_blocked(self, gh):
        gh["repo"] = None
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.blocked is True
        assert "does not exist" in st.summary

    def test_archived_repo_is_blocked(self, gh):
        gh["repo"] = _repo(archived=True)
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.blocked is True
        assert "archived" in st.summary

    def test_no_push_permission_is_blocked(self, gh):
        """A token can pass /user and still not be able to tag THIS repo."""
        gh["repo"] = _repo(push=False)
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.blocked is True
        assert "cannot push" in st.summary

    def test_existing_tag_blocks_a_fresh_release(self, gh):
        gh["tag_exists"] = True
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok")
        assert st.blocked is True
        assert "release:resume" in _text(st)

    def test_existing_tag_is_expected_when_resuming(self, gh):
        gh["tag_exists"] = True
        gh["release"] = {"html_url": "https://gh/r/1"}
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok", resume=True)
        assert st.ok is True
        assert st.tag_exists is True
        assert st.already_published is True

    def test_resume_reports_missing_release(self, gh):
        gh["tag_exists"] = True
        gh["release"] = None
        st = release_gate.check_github("org/repo", "v1.0.0", token="tok", resume=True)
        assert st.ok is True
        assert st.already_published is False

    def test_malformed_repo_reference(self, gh):
        st = release_gate.check_github("notarepo", "v1.0.0", token="tok")
        assert st.blocked is True

    # ── Version regression ────────────────────────────────────────────

    def test_a_version_below_the_newest_tag_is_refused(self, gh):
        """The wizard offering v0.0.1 for a repo at v0.2.0 must not get through.

        A tag cannot be taken back, so a version that goes backwards is
        permanent. This is the last line of defence behind a tag lookup that
        failed and answered "no tags found".
        """
        gh["latest_tag"] = "v0.2.0"
        st = release_gate.check_github("org/repo", "v0.0.1", token="tok")
        assert st.blocked is True
        assert "older than v0.2.0" in st.summary

    def test_a_version_above_the_newest_tag_is_fine(self, gh):
        gh["latest_tag"] = "v0.2.0"
        st = release_gate.check_github("org/repo", "v0.3.0", token="tok")
        assert st.ok is True

    def test_a_failed_tag_lookup_is_a_refusal(self, gh):
        """"GitHub did not answer" must never be read as "there are no tags"."""
        gh["latest_tag"] = registry.RegistryError("boom", status=500)
        st = release_gate.check_github("org/repo", "v0.0.1", token="tok")
        assert st.blocked is True
        assert "500" in st.summary


# ── PyPI channel ──────────────────────────────────────────────────────


class TestCheckPyPI:
    def test_existing_project_new_version_is_ready(self, pypi):
        st = release_gate.check_pypi(
            "pkg", "1.6.1", username="__token__", password="pypi-x"
        )
        assert st.ok is True
        assert st.new_project is False
        assert st.unproven is False

    def test_missing_credentials_is_a_refusal(self, pypi):
        st = release_gate.check_pypi("pkg", "1.0.0", username=None, password=None)
        assert st.blocked is True
        assert "twine_username" in _text(st)
        assert "--no-pypi" in _text(st)

    def test_429_is_a_rate_limit_never_a_credential_problem(self, pypi):
        pypi["probe"] = _probe(429, rate_limited=True, retry_after="600")
        st = release_gate.check_pypi(
            "pkg", "1.0.0", username="__token__", password="pypi-x"
        )
        assert st.blocked is True
        assert st.rate_limited is True
        assert "429" in st.summary
        assert "invalid" not in _text(st)
        assert "not a credential problem" in _text(st)
        # What is known about the window is reported; nothing is invented.
        assert "600" in _text(st)

    def test_no_retry_after_says_the_window_is_unknown(self, pypi):
        pypi["probe"] = _probe(429, rate_limited=True)
        st = release_gate.check_pypi(
            "pkg", "1.0.0", username="__token__", password="pypi-x"
        )
        assert "unknown" in _text(st)

    def test_401_is_credentials_never_a_rate_limit(self, pypi):
        pypi["probe"] = _probe(401)
        st = release_gate.check_pypi("pkg", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert st.rate_limited is False
        assert "rate limit" not in _text(st)

    def test_403_is_credentials(self, pypi):
        pypi["probe"] = _probe(403)
        st = release_gate.check_pypi("pkg", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert st.rate_limited is False

    def test_unusable_answer_is_a_refusal_not_a_warning(self, pypi):
        """Doubt costs a wait. A false green costs a permanent divergence."""
        pypi["probe"] = _probe(503)
        st = release_gate.check_pypi("pkg", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert "503" in st.summary

    def test_unreachable_pypi_is_a_refusal(self, pypi):
        pypi["probe"] = registry.RegistryError("Network error: down")
        st = release_gate.check_pypi("pkg", "1.0.0", username="u", password="p")
        assert st.blocked is True

    def test_rate_limited_project_lookup_is_a_rate_limit(self, pypi):
        pypi["project_exists"] = registry.RegistryError(
            "PyPI API error (HTTP 429)", status=429, rate_limited=True
        )
        st = release_gate.check_pypi("pkg", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert st.rate_limited is True

    def test_already_published_version_blocks_a_fresh_release(self, pypi):
        pypi["version_exists"] = True
        st = release_gate.check_pypi("pkg", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert "already on PyPI" in st.summary

    def test_already_published_version_is_done_when_resuming(self, pypi):
        pypi["version_exists"] = True
        st = release_gate.check_pypi(
            "pkg", "1.0.0", username="u", password="p", resume=True
        )
        assert st.ok is True
        assert st.already_published is True

    def test_version_lookup_rate_limit_is_never_read_as_published(self, pypi):
        pypi["version_exists"] = registry.RegistryError(
            "PyPI API error (HTTP 429)", status=429, rate_limited=True
        )
        st = release_gate.check_pypi("pkg", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert st.rate_limited is True
        assert st.already_published is False

    def test_leading_v_is_stripped_for_pypi(self, pypi, monkeypatch):
        seen = {}

        def _version_exists(package, version, strict=False):
            seen["version"] = version
            return False

        monkeypatch.setattr(registry, "pypi_version_exists", _version_exists)
        release_gate.check_pypi("pkg", "v2.3.4", username="u", password="p")
        assert seen["version"] == "2.3.4"

    # ── The new-project case, which is the one that actually failed ───

    def test_the_probe_names_the_project_so_creation_is_exercised(self, pypi, monkeypatch):
        """A probe with no project name cannot see the creation rate limit.

        PyPI keys the limiter that has been refusing these releases on the
        project NAME. A fileless upload that carries no name is turned away for
        the missing file long before any name is evaluated, so it can only ever
        answer "the credentials are fine".
        """
        seen = {}

        def _probe_fn(user, password, *, package=None, version=None, **kw):
            seen["package"] = package
            seen["version"] = version
            return _probe(400, named=bool(package))

        monkeypatch.setattr(registry, "pypi_upload_probe", _probe_fn)
        pypi["project_exists"] = False
        release_gate.check_pypi(
            "splent_feature_team", "0.2.1", username="u", password="p"
        )
        assert seen["package"] == "splent_feature_team"
        assert seen["version"] == "0.2.1"

    def test_new_project_rate_limit_is_refused_not_flagged(self, pypi):
        """The exact state of the incident: 13 new projects, creation 429ing."""
        pypi["project_exists"] = False
        pypi["probe"] = _probe(429, rate_limited=True, named=True)
        st = release_gate.check_pypi(
            "splent_feature_team", "0.2.1", username="u", password="p"
        )
        assert st.blocked is True
        assert st.rate_limited is True
        assert st.ok is False
        assert "new projects" in st.summary
        assert "not a credential problem" in _text(st)

    def test_new_project_accepted_by_a_named_probe_is_proven(self, pypi):
        pypi["project_exists"] = False
        st = release_gate.check_pypi("brand_new", "0.1.0", username="u", password="p")
        assert st.ok is True
        assert st.new_project is True
        assert st.unproven is False
        assert "would create brand_new" in st.summary

    def test_shallow_probe_admits_the_new_project_is_unproven(self, pypi):
        """The cheap first pass says what it did not check instead of implying it did."""
        pypi["project_exists"] = False
        st = release_gate.check_pypi(
            "brand_new", "0.1.0", username="u", password="p", deep=False
        )
        assert st.ok is True
        assert st.unproven is True
        assert "was not verified" in _text(st)

    def test_wrongly_scoped_token_is_refused(self, pypi):
        """A project-scoped token is only valid for the project it names."""
        pypi["probe"] = _probe(403, named=True)
        st = release_gate.check_pypi(
            "splent_feature_team", "0.2.1", username="u", password="p"
        )
        assert st.blocked is True
        assert st.rate_limited is False
        assert "not allowed to upload splent_feature_team" in st.summary
        assert "project-scoped" in _text(st)


# ── Docker Hub channel ────────────────────────────────────────────────


class TestCheckDocker:
    def test_missing_credentials_is_a_refusal(self):
        st = release_gate.check_docker("me/app", "1.0.0", username=None, password=None)
        assert st.blocked is True
        assert "DOCKERHUB_USERNAME" in " ".join(st.hints)

    def test_healthy_login_is_ready(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "dockerhub_login_probe",
            lambda u, p, timeout=10: registry.DockerProbe(status=200, rate_limited=False),
        )
        monkeypatch.setattr(
            registry, "dockerhub_tag_exists", lambda ns, repo, tag, timeout=10: False
        )
        st = release_gate.check_docker("me/app", "1.0.0", username="u", password="p")
        assert st.ok is True

    def test_401_is_credentials(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "dockerhub_login_probe",
            lambda u, p, timeout=10: registry.DockerProbe(status=401, rate_limited=False),
        )
        st = release_gate.check_docker("me/app", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert st.rate_limited is False

    def test_429_is_a_rate_limit(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "dockerhub_login_probe",
            lambda u, p, timeout=10: registry.DockerProbe(status=429, rate_limited=True),
        )
        st = release_gate.check_docker("me/app", "1.0.0", username="u", password="p")
        assert st.blocked is True
        assert st.rate_limited is True
        assert "not a credential problem" in _text(st)

    def test_already_pushed_tag_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "dockerhub_login_probe",
            lambda u, p, timeout=10: registry.DockerProbe(status=200, rate_limited=False),
        )
        monkeypatch.setattr(
            registry, "dockerhub_tag_exists", lambda ns, repo, tag, timeout=10: True
        )
        st = release_gate.check_docker("me/app", "1.0.0", username="u", password="p")
        assert st.ok is True
        assert st.already_published is True


# ── run_gate ──────────────────────────────────────────────────────────


class TestRunGate:
    def test_both_channels_ready(self, gh, pypi):
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="1.0.0",
            token="tok",
            pypi_username="u",
            pypi_password="p",
        )
        assert report.ok is True
        assert report.blocked == []

    def test_one_blocked_channel_blocks_the_release(self, gh, pypi):
        pypi["probe"] = _probe(429, rate_limited=True)
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="1.0.0",
            token="tok",
            pypi_username="u",
            pypi_password="p",
        )
        assert report.ok is False
        assert [st.channel for st in report.blocked] == [release_gate.PYPI]
        assert report.rate_limited

    def test_excluded_channel_is_skipped_not_checked(self, gh, monkeypatch):
        """--no-pypi must not even ask PyPI, and must not count as a failure."""

        def _boom(*a, **k):
            raise AssertionError("PyPI must not be contacted when it is off")

        monkeypatch.setattr(registry, "pypi_upload_probe", _boom)
        monkeypatch.setattr(registry, "pypi_project_exists", _boom)
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="1.0.0",
            channels=("github",),
            token="tok",
        )
        assert report.ok is True
        assert [st.channel for st in report.skipped] == [release_gate.PYPI]
        # Docker is not a channel of this release, so it is not announced at all.
        assert report.docker is None

    def test_empty_gate_is_never_ok(self):
        assert release_gate.GateReport().ok is False

    def test_tag_is_derived_from_the_version(self, gh, pypi):
        seen = {}

        def _tag_exists(org, name, tag, token=None):
            seen["tag"] = tag
            return False

        import unittest.mock

        with unittest.mock.patch.object(registry, "github_tag_exists", _tag_exists):
            release_gate.run_gate(
                repo="org/repo",
                package="pkg",
                version="1.2.3",
                token="tok",
                pypi_username="u",
                pypi_password="p",
            )
        assert seen["tag"] == "v1.2.3"

    def test_credentials_come_from_the_environment_by_default(
        self, gh, pypi, monkeypatch
    ):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-x")
        report = release_gate.run_gate(repo="org/repo", package="pkg", version="1.0.0")
        assert report.github.blocked is True
        assert report.pypi.ok is True

    def test_docker_is_checked_when_it_is_a_declared_channel(
        self, gh, pypi, monkeypatch
    ):
        """The image is published last, so it is the channel most able to diverge."""
        monkeypatch.setattr(
            registry,
            "dockerhub_login_probe",
            lambda u, p, timeout=10: registry.DockerProbe(status=401, rate_limited=False),
        )
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="1.0.0",
            channels=("github", "pypi", "docker"),
            token="tok",
            pypi_username="u",
            pypi_password="p",
            docker_image="me/app",
            docker_username="u",
            docker_password="p",
        )
        assert report.ok is False
        assert [st.channel for st in report.blocked] == [release_gate.DOCKER]

    # ── Bumping past an unfinished release ────────────────────────────

    def test_an_unfinished_previous_release_blocks_a_new_version(self, gh, pypi):
        """This is how features reached v0.2.1 with nothing on PyPI.

        The check lives in the gate rather than in the wizard, so it also
        applies to `feature:release <name> 0.3.0`, which never opens the wizard.
        """
        gh["latest_tag"] = "v0.2.1"
        pypi["version_exists"] = False
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="0.3.0",
            token="tok",
            pypi_username="u",
            pypi_password="p",
        )
        assert report.ok is False
        assert report.unfinished is not None
        assert report.unfinished.tag == "v0.2.1"
        assert "PyPI" in report.unfinished.missing

    def test_allow_unfinished_is_an_explicit_escape_hatch(self, gh, pypi):
        gh["latest_tag"] = "v0.2.1"
        pypi["version_exists"] = False
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="0.3.0",
            token="tok",
            pypi_username="u",
            pypi_password="p",
            allow_unfinished=True,
        )
        assert report.ok is True
        assert report.unfinished is not None

    def test_a_finished_previous_release_does_not_block(self, gh, pypi):
        gh["latest_tag"] = "v0.2.1"
        pypi["version_exists"] = {"0.2.1": True, "0.3.0": False}
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="0.3.0",
            token="tok",
            pypi_username="u",
            pypi_password="p",
        )
        assert report.unfinished is None
        assert report.ok is True

    def test_an_unanswerable_previous_release_is_a_refusal(self, gh, pypi):
        """Silence about the previous release is exactly how a number gets burned."""
        gh["latest_tag"] = "v0.2.1"
        pypi["version_exists"] = registry.RegistryError("boom", status=500)
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="0.3.0",
            token="tok",
            pypi_username="u",
            pypi_password="p",
        )
        assert report.ok is False
        assert "previous release finished" in report.github.summary

    def test_resuming_never_looks_for_an_unfinished_release(self, gh, pypi):
        gh["latest_tag"] = "v0.2.1"
        gh["tag_exists"] = True
        pypi["version_exists"] = False
        report = release_gate.run_gate(
            repo="org/repo",
            package="pkg",
            version="0.2.1",
            token="tok",
            pypi_username="u",
            pypi_password="p",
            resume=True,
        )
        assert report.unfinished is None
        assert report.ok is True


# ── Refusal messages ──────────────────────────────────────────────────


class TestRefuse:
    def _report(self, statuses, **kw):
        return release_gate.GateReport(statuses=statuses, **kw)

    def test_a_rate_limit_and_a_credential_failure_are_never_merged(self, capsys):
        """The headline must not tell the operator to wait out a 401."""
        report = self._report(
            [
                release_gate.ChannelStatus(
                    release_gate.GITHUB,
                    ok=False,
                    summary="blocked, GITHUB_TOKEN was rejected (HTTP 401)",
                ),
                release_gate.ChannelStatus(
                    release_gate.PYPI,
                    ok=False,
                    rate_limited=True,
                    summary="blocked, PyPI is rate limiting this account (HTTP 429)",
                ),
            ]
        )
        with pytest.raises(SystemExit):
            release_gate.refuse(report)
        out = capsys.readouterr().out
        assert "pypi is RATE LIMITED" in out
        # The 401 is named as a different problem, in the same breath.
        assert "github is a different problem" in out
        assert "waiting will not fix it" in out
        # And it is never described as fine.
        assert "The credentials are fine, the channel" not in out

    def test_a_pure_rate_limit_still_says_the_credentials_are_fine(self, capsys):
        report = self._report(
            [
                release_gate.ChannelStatus(release_gate.GITHUB, ok=True),
                release_gate.ChannelStatus(
                    release_gate.PYPI, ok=False, rate_limited=True, summary="429"
                ),
            ]
        )
        with pytest.raises(SystemExit):
            release_gate.refuse(report)
        out = capsys.readouterr().out
        assert "Those credentials are fine" in out
        assert "different problem" not in out

    def test_an_unfinished_release_names_the_resume_command(self, capsys):
        report = self._report(
            [release_gate.ChannelStatus(release_gate.GITHUB, ok=True)],
            unfinished=release_gate.IncompleteRelease(tag="v0.2.1", missing=["PyPI"]),
        )
        with pytest.raises(SystemExit):
            release_gate.refuse(report, resume_target="splent_feature_team")
        out = capsys.readouterr().out
        assert "splent release:resume splent_feature_team" in out
        assert "--allow-unfinished" in out


# ── Declared channels ─────────────────────────────────────────────────


class TestDeclaredChannels:
    def _write(self, tmp_path, body):
        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "x"\n' + body)
        return str(p)

    def test_defaults_to_both(self, tmp_path):
        assert (
            release_gate.declared_channels(self._write(tmp_path, ""))
            == release_gate.DEFAULT_CHANNELS
        )

    def test_missing_file_defaults_to_both(self, tmp_path):
        assert (
            release_gate.declared_channels(str(tmp_path / "nope.toml"))
            == release_gate.DEFAULT_CHANNELS
        )

    def test_github_only_declaration(self, tmp_path):
        path = self._write(tmp_path, '\n[tool.splent.release]\nchannels = ["github"]\n')
        assert release_gate.declared_channels(path) == ("github",)

    def test_a_misspelled_channel_is_an_error_not_a_silent_default(self, tmp_path):
        """Falling back to both would publish a private package to PyPI forever."""
        path = self._write(tmp_path, '\n[tool.splent.release]\nchannels = ["githb"]\n')
        with pytest.raises(release_gate.ChannelDeclarationError) as e:
            release_gate.declared_channels(path)
        assert "githb" in str(e.value)

    def test_a_misspelled_key_is_an_error(self, tmp_path):
        path = self._write(tmp_path, '\n[tool.splent.release]\nchanels = ["github"]\n')
        with pytest.raises(release_gate.ChannelDeclarationError) as e:
            release_gate.declared_channels(path)
        assert "chanels" in str(e.value)

    def test_an_empty_list_is_an_error(self, tmp_path):
        path = self._write(tmp_path, "\n[tool.splent.release]\nchannels = []\n")
        with pytest.raises(release_gate.ChannelDeclarationError):
            release_gate.declared_channels(path)

    def test_a_string_instead_of_a_list_is_an_error(self, tmp_path):
        path = self._write(tmp_path, '\n[tool.splent.release]\nchannels = "github"\n')
        with pytest.raises(release_gate.ChannelDeclarationError):
            release_gate.declared_channels(path)

    def test_normalize_keeps_canonical_order(self):
        assert release_gate.normalize_channels(["pypi", "github"]) == (
            "github",
            "pypi",
        )

    def test_normalize_none_is_the_default_pair(self):
        assert release_gate.normalize_channels(None) == release_gate.DEFAULT_CHANNELS


# ── Credentials ───────────────────────────────────────────────────────


class TestPyPICredentials:
    def test_twine_names_win(self, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "t-user")
        monkeypatch.setenv("TWINE_PASSWORD", "t-pass")
        monkeypatch.setenv("PYPI_USERNAME", "p-user")
        assert release_gate.pypi_credentials() == ("t-user", "t-pass")

    def test_pypi_names_are_accepted_as_a_fallback(self, monkeypatch):
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.setenv("PYPI_USERNAME", "p-user")
        monkeypatch.setenv("PYPI_PASSWORD", "p-pass")
        assert release_gate.pypi_credentials() == ("p-user", "p-pass")

    def test_nothing_set_is_nothing(self, monkeypatch):
        for name in (
            "TWINE_USERNAME",
            "TWINE_PASSWORD",
            "PYPI_USERNAME",
            "PYPI_PASSWORD",
        ):
            monkeypatch.delenv(name, raising=False)
        assert release_gate.pypi_credentials() == (None, None)


# ── Windows ───────────────────────────────────────────────────────────


class TestWindows:
    def test_github_window_uses_the_reported_reset(self):
        text = release_gate.github_window(int(time.time()) + 3600)
        assert "resets at" in text
        assert "min from now" in text

    def test_github_window_without_a_reset_says_so(self):
        assert "did not report" in release_gate.github_window(None)

    def test_pypi_window_reports_retry_after_when_present(self):
        assert "900" in release_gate.pypi_window("900")

    def test_pypi_window_admits_it_is_unknown(self):
        text = release_gate.pypi_window(None)
        assert "unknown" in text
        # No invented duration.
        assert "hour" not in text
        assert "minute" not in text


# ── Incomplete releases ───────────────────────────────────────────────


class TestFindIncompleteRelease:
    @pytest.fixture(autouse=True)
    def _tag(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "latest_semver_tag",
            lambda org, repo, token=None, strict=False: "v0.2.1",
        )

    def test_tag_without_pypi_is_incomplete(self, monkeypatch):
        monkeypatch.setattr(
            registry, "pypi_version_exists", lambda p, v, strict=False: False
        )
        monkeypatch.setattr(
            registry, "github_release_by_tag", lambda o, r, t, token=None: {"id": 1}
        )
        found = release_gate.find_incomplete_release("org/repo", "pkg")
        assert found is not None
        assert found.tag == "v0.2.1"
        assert found.missing == ["PyPI"]

    def test_tag_without_github_release_is_incomplete(self, monkeypatch):
        monkeypatch.setattr(
            registry, "pypi_version_exists", lambda p, v, strict=False: True
        )
        monkeypatch.setattr(
            registry, "github_release_by_tag", lambda o, r, t, token=None: None
        )
        found = release_gate.find_incomplete_release("org/repo", "pkg")
        assert found is not None
        assert found.missing == ["the GitHub release"]

    def test_fully_published_is_not_incomplete(self, monkeypatch):
        monkeypatch.setattr(
            registry, "pypi_version_exists", lambda p, v, strict=False: True
        )
        monkeypatch.setattr(
            registry, "github_release_by_tag", lambda o, r, t, token=None: {"id": 1}
        )
        assert release_gate.find_incomplete_release("org/repo", "pkg") is None

    def test_pypi_ignored_when_the_package_does_not_publish_there(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("PyPI must not be consulted for a github-only package")

        monkeypatch.setattr(registry, "pypi_version_exists", _boom)
        monkeypatch.setattr(
            registry, "github_release_by_tag", lambda o, r, t, token=None: {"id": 1}
        )
        assert (
            release_gate.find_incomplete_release(
                "org/repo", "pkg", channels=("github",)
            )
            is None
        )

    def test_network_failure_is_quiet_by_default(self, monkeypatch):
        def _raise(*a, **k):
            raise registry.RegistryError("down")

        monkeypatch.setattr(registry, "pypi_version_exists", _raise)
        assert release_gate.find_incomplete_release("org/repo", "pkg") is None

    def test_network_failure_is_loud_when_strict(self, monkeypatch):
        """A guard that no-ops on 401 does not guard anything."""

        def _raise(*a, **k):
            raise registry.RegistryError("down", status=401)

        monkeypatch.setattr(registry, "pypi_version_exists", _raise)
        with pytest.raises(registry.RegistryError):
            release_gate.find_incomplete_release("org/repo", "pkg", strict=True)

    def test_a_failed_tag_lookup_is_loud_when_strict(self, monkeypatch):
        def _raise(*a, **k):
            raise registry.RegistryError("down", status=401)

        monkeypatch.setattr(registry, "latest_semver_tag", _raise)
        with pytest.raises(registry.RegistryError):
            release_gate.find_incomplete_release("org/repo", "pkg", strict=True)

    def test_no_tags_is_not_incomplete(self, monkeypatch):
        monkeypatch.setattr(
            registry,
            "latest_semver_tag",
            lambda org, repo, token=None, strict=False: None,
        )
        assert release_gate.find_incomplete_release("org/repo", "pkg") is None

    def test_the_version_being_released_is_not_its_own_predecessor(self, monkeypatch):
        monkeypatch.setattr(
            registry, "pypi_version_exists", lambda p, v, strict=False: False
        )
        assert (
            release_gate.find_incomplete_release(
                "org/repo", "pkg", skip_tag="v0.2.1"
            )
            is None
        )
