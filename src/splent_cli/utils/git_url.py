"""
Shared Git URL + clone helpers for SPLENT CLI.

Two related concerns live here:

1. clone()  — preferred for fetching feature code into the workspace
   (feature:clone, and therefore product:resolve / product:sync / feature:fork).
   Always tries SSH first and, if SSH cannot reach the repo, falls back to HTTPS
   (with GITHUB_TOKEN if set, otherwise anonymous). The SSH-vs-HTTPS decision is
   made PER REPO from the actual clone result — NOT from a global probe.

   Why per-repo and not a global `ssh -T git@github.com` probe: that probe only
   proves the user can authenticate to GitHub *at all*, not that their key can
   read a specific private repo (e.g. a feature in an org they are not a member
   of). The old global probe made splent commit to SSH and then fail, instead of
   trying HTTPS — exactly the "it worked for me, not for the room" failure mode.

2. build_git_url() / _ssh_available()  — legacy single-url builder still used by
   the release / install / upgrade / unlock paths. Kept for compatibility.
"""

import os
import shutil
import subprocess

import click

from splent_cli.utils.proc import run


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def ssh_url(namespace: str, repo: str) -> str:
    return f"git@github.com:{namespace}/{repo}.git"


def https_url(namespace: str, repo: str) -> tuple[str, str]:
    """Return (real_url, display_url). display_url never contains the token."""
    token = os.getenv("GITHUB_TOKEN")
    display = f"https://github.com/{namespace}/{repo}.git"
    if token:
        return f"https://{token}@github.com/{namespace}/{repo}.git", display
    return display, display


def candidate_urls(namespace: str, repo: str) -> list[tuple[str, str, str]]:
    """Ordered clone candidates: SSH first, then HTTPS.

    Each item is (real_url, display_url, transport).
    """
    ssh = ssh_url(namespace, repo)
    https_real, https_display = https_url(namespace, repo)
    return [
        (ssh, ssh, "ssh"),
        (https_real, https_display, "https"),
    ]


# ---------------------------------------------------------------------------
# git stderr classification
# ---------------------------------------------------------------------------


def _is_ref_not_found(stderr_lc: str) -> bool:
    """The repo was reached but the requested tag/branch does not exist."""
    return (
        "remote branch" in stderr_lc and "not found" in stderr_lc
    ) or "could not find remote branch" in stderr_lc


def _is_access_or_network(stderr_lc: str) -> bool:
    """Auth / permission / network failure — a different transport may help."""
    return any(
        t in stderr_lc
        for t in (
            "authentication failed",
            "permission denied",
            "could not read from remote repository",
            "repository not found",
            "could not resolve host",
            "connection timed out",
            "connection refused",
            "network is unreachable",
            "failed to connect",
            "ssl",
            "tls",
        )
    )


# ---------------------------------------------------------------------------
# Clone with SSH-first, HTTPS-fallback (decided per repo)
# ---------------------------------------------------------------------------

CLONE_SUCCESS = "success"  # cloned OK
CLONE_REF_NOT_FOUND = "ref_not_found"  # repo reachable, ref missing
CLONE_FAILED = "failed"  # no transport could reach the repo


def clone(
    namespace: str,
    repo: str,
    dest: str,
    ref: str | None = None,
    depth: int = 1,
) -> tuple[str, str, str]:
    """Clone <namespace>/<repo> into ``dest``, trying SSH then HTTPS.

    Returns ``(outcome, display_url, stderr)``:
      * ``CLONE_SUCCESS``       — cloned; ``display_url`` is the transport used.
      * ``CLONE_REF_NOT_FOUND`` — a transport reached the repo but ``ref`` is
                                  missing; the caller may retry with ``ref=None``
                                  to get the default branch.
      * ``CLONE_FAILED``        — neither SSH nor HTTPS could reach the repo
                                  (``stderr`` holds the last error).
    """
    last_stderr = ""
    for real_url, display_url, transport in candidate_urls(namespace, repo):
        cmd = [
            "git",
            "-c",
            "advice.detachedHead=false",
            "clone",
            "--depth",
            str(depth),
        ]
        if ref:
            cmd += ["--branch", ref]
        cmd += ["--quiet", real_url, dest]

        result = run(cmd, check=False, capture=True)
        if result.returncode == 0:
            return CLONE_SUCCESS, display_url, ""

        stderr = (result.stderr or result.stdout or "").strip()
        last_stderr = stderr
        # Remove any partial checkout before retrying with another transport.
        shutil.rmtree(dest, ignore_errors=True)

        stderr_lc = stderr.lower()
        if _is_ref_not_found(stderr_lc) and not _is_access_or_network(
            stderr_lc
        ):
            # The repo IS reachable over this transport; the ref simply doesn't
            # exist. Another transport would hit the same missing ref → stop.
            return CLONE_REF_NOT_FOUND, display_url, stderr

        # Access / network / unknown failure → try the next transport (HTTPS).
        if transport == "ssh":
            click.secho(
                "  SSH could not reach the repo — trying HTTPS…", fg="yellow"
            )

    return CLONE_FAILED, "", last_stderr


# ---------------------------------------------------------------------------
# Legacy single-url builder (release / install / upgrade / unlock)
# ---------------------------------------------------------------------------

# Cache the SSH probe for the lifetime of the process: the probe spawns
# `ssh -T git@github.com` (up to ~10s when offline) and can otherwise run on
# every git op. None = not yet probed.
_ssh_available_cache: bool | None = None


def _ssh_available() -> bool:
    """Check if SSH access to github.com works (key loaded, agent running).

    NOTE: this only proves the user can authenticate to GitHub at all, not that
    they can read a specific private repo. For fetching features prefer
    ``clone()`` above, which falls back to HTTPS per repo. This helper remains
    for the push-oriented paths (release/unlock) where HTTPS-anonymous is not a
    meaningful fallback.
    """
    global _ssh_available_cache
    if _ssh_available_cache is not None:
        return _ssh_available_cache

    try:
        result = subprocess.run(
            [
                "ssh",
                "-T",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ConnectTimeout=5",
                "git@github.com",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        _ssh_available_cache = (
            "successfully authenticated" in result.stderr.lower()
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _ssh_available_cache = False

    return _ssh_available_cache


def build_git_url(namespace: str, repo: str) -> tuple[str, str]:
    """Build a single Git URL, trying SSH first with HTTPS fallback.

    Returns (real_url, display_url) where display_url never contains tokens.
    Prefer ``clone()`` for fetching feature code; this is kept for callers that
    need a single URL string (release/install/upgrade/unlock).
    """
    if _ssh_available():
        url = ssh_url(namespace, repo)
        return url, url

    token = os.getenv("GITHUB_TOKEN")
    if token:
        click.secho(
            "  SSH not available — using HTTPS with token.", fg="yellow"
        )
        return https_url(namespace, repo)

    click.secho(
        "  SSH not available and no GITHUB_TOKEN found — using HTTPS (read-only).",
        fg="yellow",
    )
    return https_url(namespace, repo)
