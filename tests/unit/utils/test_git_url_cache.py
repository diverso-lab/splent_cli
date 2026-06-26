"""Tests for splent_cli.utils.git_url.build_git_url / _ssh_available.

Hardened behaviors covered first:
  * The SSH probe (`ssh -T git@github.com`) result is memoized for the lifetime
    of the process: it runs AT MOST ONCE across many build_git_url calls.
  * SSH available -> ssh url (real == display).
  * SSH unavailable + GITHUB_TOKEN -> https with token in real_url, while the
    display_url NEVER leaks the token.
  * SSH unavailable + no token -> plain https (real == display, no token).

The module `import subprocess` directly, so we patch git_url.subprocess.run.
No real ssh / git / network is required. The module-level probe cache is reset
before every test so cases don't bleed into each other.
"""

import subprocess

import pytest

from splent_cli.utils import git_url


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    """Reset the process-wide SSH probe memo before (and after) each test."""
    git_url._ssh_available_cache = None
    yield
    git_url._ssh_available_cache = None


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fake_ssh_success():
    # GitHub returns exit code 1 with this banner on a working key.
    return _completed(
        returncode=1,
        stderr="Hi octocat! You've successfully authenticated, but GitHub does "
        "not provide shell access.",
    )


def _fake_ssh_failure():
    return _completed(returncode=255, stderr="Permission denied (publickey).")


# --------------------------------------------------------------------------- #
# Hardened: probe is memoized (runs at most once per process)
# --------------------------------------------------------------------------- #
class TestProbeCaching:
    def test_probe_runs_at_most_once_across_many_calls_success(self, monkeypatch):
        calls = {"n": 0}

        def fake_run(*a, **k):
            calls["n"] += 1
            return _fake_ssh_success()

        monkeypatch.setattr(git_url.subprocess, "run", fake_run)

        for _ in range(5):
            git_url.build_git_url("acme", "widgets")

        assert calls["n"] == 1

    def test_probe_runs_at_most_once_across_many_calls_failure(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        calls = {"n": 0}

        def fake_run(*a, **k):
            calls["n"] += 1
            return _fake_ssh_failure()

        monkeypatch.setattr(git_url.subprocess, "run", fake_run)

        for _ in range(4):
            git_url.build_git_url("acme", "widgets")

        assert calls["n"] == 1

    def test_ssh_available_memoizes_failure_branch(self, monkeypatch):
        """A False result must also be cached (cache uses None as sentinel)."""
        calls = {"n": 0}

        def fake_run(*a, **k):
            calls["n"] += 1
            return _fake_ssh_failure()

        monkeypatch.setattr(git_url.subprocess, "run", fake_run)

        assert git_url._ssh_available() is False
        assert git_url._ssh_available() is False
        assert calls["n"] == 1

    def test_probe_timeout_treated_as_unavailable_and_cached(self, monkeypatch):
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            raise subprocess.TimeoutExpired(cmd="ssh", timeout=10)

        monkeypatch.setattr(git_url.subprocess, "run", boom)

        assert git_url._ssh_available() is False
        assert git_url._ssh_available() is False
        assert calls["n"] == 1

    def test_probe_missing_ssh_binary_treated_as_unavailable(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(git_url.subprocess, "run", boom)
        assert git_url._ssh_available() is False


# --------------------------------------------------------------------------- #
# Branch: SSH available -> ssh url
# --------------------------------------------------------------------------- #
class TestSshAvailable:
    def test_returns_ssh_url_when_authenticated(self, monkeypatch):
        monkeypatch.setattr(
            git_url.subprocess, "run", lambda *a, **k: _fake_ssh_success()
        )
        real, display = git_url.build_git_url("acme", "widgets")
        assert real == "git@github.com:acme/widgets.git"
        assert display == real

    def test_ssh_preferred_even_when_token_present(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "supersecret")
        monkeypatch.setattr(
            git_url.subprocess, "run", lambda *a, **k: _fake_ssh_success()
        )
        real, display = git_url.build_git_url("acme", "widgets")
        assert real.startswith("git@github.com:")
        assert "supersecret" not in real
        assert "supersecret" not in display


# --------------------------------------------------------------------------- #
# Branch: SSH unavailable + GITHUB_TOKEN -> https with token (hidden in display)
# --------------------------------------------------------------------------- #
class TestHttpsWithToken:
    def test_token_used_in_real_url_only(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_TOKEN123")
        monkeypatch.setattr(
            git_url.subprocess, "run", lambda *a, **k: _fake_ssh_failure()
        )
        real, display = git_url.build_git_url("acme", "widgets")
        assert real == "https://ghp_TOKEN123@github.com/acme/widgets.git"
        assert display == "https://github.com/acme/widgets.git"
        # The token must never appear in the display URL.
        assert "ghp_TOKEN123" not in display


# --------------------------------------------------------------------------- #
# Branch: SSH unavailable + no token -> plain https
# --------------------------------------------------------------------------- #
class TestHttpsNoToken:
    def test_plain_https_when_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(
            git_url.subprocess, "run", lambda *a, **k: _fake_ssh_failure()
        )
        real, display = git_url.build_git_url("acme", "widgets")
        assert real == "https://github.com/acme/widgets.git"
        assert display == real
        assert "@" not in real.split("//", 1)[1].split("/", 1)[0]
