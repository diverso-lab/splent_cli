"""
Shared Git URL builder for SPLENT CLI.

Strategy: try SSH first, if it fails ask the user whether to fall back to HTTPS.
This removes the need for the SPLENT_USE_SSH environment variable.
"""

import os
import subprocess

import click

# Cache the SSH probe for the lifetime of the process: the probe spawns
# `ssh -T git@github.com` (up to ~10s when offline) and can otherwise run on
# every git op. None = not yet probed.
_ssh_available_cache: bool | None = None


def _ssh_available() -> bool:
    """Check if SSH access to github.com works (key loaded, agent running).

    The result is memoized for the process so the (potentially ~10s) probe
    runs at most once per invocation.
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
        # GitHub returns exit code 1 with "successfully authenticated"
        _ssh_available_cache = "successfully authenticated" in result.stderr.lower()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        _ssh_available_cache = False

    return _ssh_available_cache


def build_git_url(namespace: str, repo: str) -> tuple[str, str]:
    """Build a Git clone URL, trying SSH first with HTTPS fallback.

    Returns (real_url, display_url) where display_url never contains tokens.

    Strategy:
      1. Try SSH — if github.com accepts the key, use SSH.
      2. If SSH fails, warn and ask to continue with HTTPS.
      3. HTTPS uses GITHUB_TOKEN if available.
    """
    if _ssh_available():
        url = f"git@github.com:{namespace}/{repo}.git"
        return url, url

    token = os.getenv("GITHUB_TOKEN")

    if token:
        click.secho(
            "  SSH not available — using HTTPS with token.",
            fg="yellow",
        )
        real_url = f"https://{token}@github.com/{namespace}/{repo}.git"
        display_url = f"https://github.com/{namespace}/{repo}.git"
        return real_url, display_url

    click.secho(
        "  SSH not available and no GITHUB_TOKEN found — using HTTPS (read-only).",
        fg="yellow",
    )
    url = f"https://github.com/{namespace}/{repo}.git"
    return url, url
