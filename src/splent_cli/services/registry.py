"""Single boundary for remote registry lookups: GitHub and PyPI.

Every command that needs "the latest tag of a feature", "all tags", "the
repos of an org", "a file at a ref" or "the PyPI releases of a package"
goes through this module — previously each command carried its own copy
of this logic (feature:clone, feature:install, feature:search,
feature:versions, feature:outdated/upgrade and the release pipeline all
diverged slightly).

Helpers are quiet where the historical call sites were quiet (return
``None`` / ``[]`` on failure) and raise :class:`RegistryError` where the
caller decides how loud to be. ``RegistryError`` carries the HTTP status
and a ``rate_limited`` flag so commands can print an actionable message
(mentioning ``GITHUB_TOKEN``) instead of a traceback.
"""

import json
import os
import re
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"
USER_AGENT = "splent-cli"

# Prefix match on purpose (no anchor): "v1.2.3-rc1" still sorts as 1.2.3,
# matching the historical behavior of every duplicated implementation.
SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class RegistryError(Exception):
    """A GitHub API request failed (other than a plain 404)."""

    def __init__(self, message: str, *, status: int | None = None, rate_limited: bool = False):
        super().__init__(message)
        self.status = status
        self.rate_limited = rate_limited


def github_token() -> str | None:
    return os.getenv("GITHUB_TOKEN") or None


def rate_limit_hint(token: str | None) -> str:
    """Actionable hint appended to rate-limit / forbidden errors."""
    if token:
        return ""
    return " Set GITHUB_TOKEN to raise the limit and access private repos."


def _github_headers(token: str | None, *, raw: bool = False) -> dict:
    headers = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _request(url: str, headers: dict, timeout: int = 10) -> bytes | None:
    """GET *url*. Returns the body, ``None`` on 404, raises RegistryError otherwise."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        remaining = e.headers.get("X-RateLimit-Remaining") if e.headers else None
        rate_limited = e.code == 429 or (e.code == 403 and remaining == "0")
        raise RegistryError(
            f"GitHub API error (HTTP {e.code})",
            status=e.code,
            rate_limited=rate_limited,
        )
    except urllib.error.URLError as e:
        raise RegistryError(f"Network error: {e.reason}")
    except TimeoutError:
        raise RegistryError("Network error: request timed out")


def github_json(url: str, token: str | None = None, timeout: int = 10):
    """GET a GitHub API URL as parsed JSON. ``None`` on 404, RegistryError on failure."""
    body = _request(url, _github_headers(token), timeout)
    if body is None:
        return None
    return json.loads(body.decode())


# ── Repos ─────────────────────────────────────────────────────────────


def list_org_repos(org: str, token: str | None = None) -> list[dict] | None:
    """All public/accessible repos of *org* (paginated). ``None`` if the org 404s."""
    repos: list[dict] = []
    page = 1
    while True:
        url = f"{GITHUB_API}/orgs/{org}/repos?per_page=100&page={page}"
        batch = github_json(url, token)
        if batch is None:
            return None if page == 1 else repos
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def fetch_repo(org: str, repo: str, token: str | None = None) -> dict | None:
    """Repo metadata (description, html_url, pushed_at, archived…). ``None`` on 404."""
    return github_json(f"{GITHUB_API}/repos/{org}/{repo}", token)


def fetch_file(
    org: str, repo: str, path: str, ref: str | None = None, token: str | None = None
) -> str | None:
    """Raw contents of *path* in the repo at *ref* (default branch when omitted).

    ``None`` when the file, ref or repo does not exist.
    """
    url = f"{GITHUB_API}/repos/{org}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={ref}"
    body = _request(url, _github_headers(token, raw=True))
    if body is None:
        return None
    return body.decode("utf-8", errors="replace")


# ── Tags & versions ───────────────────────────────────────────────────


def list_tags(
    org: str, repo: str, token: str | None = None, max_pages: int = 50
) -> list[str]:
    """All tag names of the repo, newest first as GitHub returns them.

    Raises RegistryError on network/API failure; ``[]`` when the repo has
    no tags or does not exist.
    """
    tags: list[str] = []
    for page in range(1, max_pages + 1):
        url = f"{GITHUB_API}/repos/{org}/{repo}/tags?per_page=100&page={page}"
        batch = github_json(url, token)
        if not batch:
            break
        tags.extend(t.get("name", "") for t in batch if t.get("name"))
        if len(batch) < 100:
            break
    return tags


def semver_sorted(tags: list[str]) -> list[str]:
    """Semver tags from *tags*, highest first. Non-semver tags are dropped."""
    versions = []
    for name in tags:
        m = SEMVER_RE.match(name)
        if m:
            versions.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), name))
    versions.sort(reverse=True)
    return [v[3] for v in versions]


def list_semver_tags(
    org: str,
    repo: str,
    token: str | None = None,
    limit: int | None = None,
    quiet: bool = True,
) -> list[str]:
    """Semver tags of the repo, highest first. Quiet ``[]`` on failure by default."""
    try:
        tags = list_tags(org, repo, token)
    except RegistryError:
        if quiet:
            return []
        raise
    ordered = semver_sorted(tags)
    return ordered[:limit] if limit else ordered


def latest_semver_tag(org: str, repo: str, token: str | None = None) -> str | None:
    """Highest semver tag of the repo (first page of tags), or ``None``.

    Falls back to the newest tag when none parse as semver — the exact
    behavior of the historical per-command implementations. Never raises.
    """
    if token is None:
        token = github_token()
    try:
        batch = github_json(
            f"{GITHUB_API}/repos/{org}/{repo}/tags?per_page=100", token
        )
    except RegistryError:
        return None
    if not batch:
        return None
    tags = [t.get("name", "") for t in batch if t.get("name")]
    ordered = semver_sorted(tags)
    if ordered:
        return ordered[0]
    return tags[0] if tags else None


# ── PyPI ──────────────────────────────────────────────────────────────


def pypi_versions(package: str) -> list[str]:
    """All PyPI release versions of *package*, newest upload first. Quiet ``[]``."""
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    releases = data.get("releases", {})

    def _latest_upload(v):
        files = releases.get(v) or []
        if not files:
            return ""
        return max(f.get("upload_time", "") for f in files)

    return sorted(releases.keys(), key=_latest_upload, reverse=True)


def pypi_version_exists(package: str, version: str) -> bool:
    """Check one version against the per-version endpoint (not CDN-cached)."""
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except (urllib.error.URLError, TimeoutError):
        return False
