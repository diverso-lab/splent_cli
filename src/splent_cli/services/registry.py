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

import base64
import dataclasses
import json
import os
import re
import urllib.error
import urllib.request

GITHUB_API = "https://api.github.com"
USER_AGENT = "splent-cli"

PYPI_UPLOAD_URL = "https://upload.pypi.org/legacy/"
TEST_PYPI_UPLOAD_URL = "https://test.pypi.org/legacy/"

# Prefix match on purpose (no anchor): "v1.2.3-rc1" still sorts as 1.2.3,
# matching the historical behavior of every duplicated implementation.
SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class RegistryError(Exception):
    """A GitHub API request failed (other than a plain 404)."""

    def __init__(
        self, message: str, *, status: int | None = None, rate_limited: bool = False
    ):
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
        "Accept": "application/vnd.github.raw+json"
        if raw
        else "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def _status_of(resp, default: int = 200) -> int:
    """The HTTP status of a response object, tolerant of stand-ins in tests."""
    status = getattr(resp, "status", None)
    return status if isinstance(status, int) else default


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

    Raises RegistryError on network/API failure, and on a repository that does
    not exist or the token cannot see (status 404). ``[]`` means the repository
    answered and has no tags, which is a different fact from "no answer" and
    must never collapse into it.
    """
    tags: list[str] = []
    for page in range(1, max_pages + 1):
        url = f"{GITHUB_API}/repos/{org}/{repo}/tags?per_page=100&page={page}"
        batch = github_json(url, token)
        if batch is None:
            if page == 1:
                raise RegistryError(
                    f"{org}/{repo} does not exist or this token cannot see it",
                    status=404,
                )
            break
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


def latest_semver_tag(
    org: str, repo: str, token: str | None = None, *, strict: bool = False
) -> str | None:
    """Highest semver tag of the repo (first page of tags), or ``None``.

    Falls back to the newest tag when none parse as semver — the exact
    behavior of the historical per-command implementations. Quiet by default;
    with ``strict=True`` a failure raises instead of answering ``None``, so
    "this repo has no tags" is never invented out of "GitHub did not answer".
    """
    if token is None:
        token = github_token()
    try:
        batch = github_json(f"{GITHUB_API}/repos/{org}/{repo}/tags?per_page=100", token)
    except RegistryError:
        if strict:
            raise
        return None
    if batch is None and strict:
        raise RegistryError(
            f"{org}/{repo} does not exist or this token cannot see it", status=404
        )
    if not batch:
        return None
    tags = [t.get("name", "") for t in batch if t.get("name")]
    ordered = semver_sorted(tags)
    if ordered:
        return ordered[0]
    return tags[0] if tags else None


# ── GitHub: identity, limits, refs and releases ───────────────────────


def github_user(token: str) -> dict | None:
    """The account behind *token*. ``None`` on 404, RegistryError on 401/403/429."""
    return github_json(f"{GITHUB_API}/user", token)


def github_rate_limit(token: str | None = None) -> dict | None:
    """Core rate-limit budget as ``{"limit", "remaining", "reset"}``.

    ``reset`` is the UNIX epoch second at which the window rolls over, which is
    what lets a caller report a real window instead of guessing one. Querying
    this endpoint does not itself consume budget. ``None`` when GitHub does not
    answer with a recognizable payload.
    """
    data = github_json(f"{GITHUB_API}/rate_limit", token)
    if not data:
        return None
    core = (data.get("resources") or {}).get("core") or data.get("rate") or {}
    if not core:
        return None
    return {
        "limit": core.get("limit"),
        "remaining": core.get("remaining"),
        "reset": core.get("reset"),
    }


def github_tag_exists(org: str, repo: str, tag: str, token: str | None = None) -> bool:
    """Whether ``refs/tags/<tag>`` exists on the remote."""
    data = github_json(f"{GITHUB_API}/repos/{org}/{repo}/git/ref/tags/{tag}", token)
    if data is None:
        return False
    # GitHub answers a prefix match with a list when the ref is ambiguous.
    if isinstance(data, list):
        return any(item.get("ref") == f"refs/tags/{tag}" for item in data)
    return data.get("ref") == f"refs/tags/{tag}"


def github_release_by_tag(
    org: str, repo: str, tag: str, token: str | None = None
) -> dict | None:
    """The published GitHub Release for *tag*, or ``None`` when there is none."""
    return github_json(f"{GITHUB_API}/repos/{org}/{repo}/releases/tags/{tag}", token)


def list_release_tags(
    org: str, repo: str, token: str | None = None, max_pages: int = 20
) -> set[str]:
    """Every tag name that has a published GitHub Release, as a set.

    One paginated call answers the question for ALL tags of the repository,
    instead of one request per tag. Raises RegistryError on failure (including
    a repository that does not exist), because "no releases" and "no answer"
    are different facts.
    """
    names: set[str] = set()
    for page in range(1, max_pages + 1):
        url = f"{GITHUB_API}/repos/{org}/{repo}/releases?per_page=100&page={page}"
        batch = github_json(url, token)
        if batch is None:
            if page == 1:
                raise RegistryError(
                    f"{org}/{repo} does not exist or this token cannot see it",
                    status=404,
                )
            break
        if not batch:
            break
        names.update(r.get("tag_name", "") for r in batch if r.get("tag_name"))
        if len(batch) < 100:
            break
    return names


@dataclasses.dataclass(frozen=True)
class ReleaseResult:
    """Outcome of creating a GitHub Release."""

    status: int
    created: bool = False
    already_exists: bool = False
    rate_limited: bool = False
    url: str | None = None
    detail: str = ""


def github_create_release(
    org: str,
    repo: str,
    *,
    tag: str,
    name: str,
    body: str,
    token: str,
    draft: bool = False,
    prerelease: bool = False,
    timeout: int = 30,
) -> ReleaseResult:
    """Create a GitHub Release for *tag*.

    Returns the outcome instead of raising for HTTP failures, so the caller can
    tell "already there" (idempotent rerun) from "refused" (a divergence, since
    the tag exists by the time this runs). Raises :class:`RegistryError` only
    when GitHub cannot be reached at all.
    """
    payload = json.dumps(
        {
            "tag_name": tag,
            "name": name,
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
        }
    ).encode()
    headers = _github_headers(token)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{org}/{repo}/releases",
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = _status_of(resp)
            try:
                data = json.loads(resp.read().decode())
            except (ValueError, UnicodeDecodeError):
                data = {}
            return ReleaseResult(
                status=status, created=True, url=(data or {}).get("html_url")
            )
    except urllib.error.HTTPError as e:
        try:
            detail = (e.read() or b"").decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        remaining = e.headers.get("X-RateLimit-Remaining") if e.headers else None
        return ReleaseResult(
            status=e.code,
            already_exists=e.code == 422 and "already_exists" in detail,
            rate_limited=e.code == 429 or (e.code == 403 and remaining == "0"),
            detail=detail[:300].strip(),
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise RegistryError(f"Network error: {getattr(e, 'reason', e)}")


# ── PyPI ──────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class PyPIProbe:
    """Outcome of a credential probe against the PyPI upload endpoint.

    ``named`` records whether the probe carried the project name and version.
    A nameless probe can only prove that the credentials are accepted by the
    endpoint; a named one additionally exercises the project lookup, the
    project-creation rate limiter and the per-project upload permission, which
    are the three things that actually refuse a release.
    """

    status: int
    rate_limited: bool
    retry_after: str | None = None
    detail: str = ""
    named: bool = False


def _multipart_body(fields: dict, boundary: str) -> bytes:
    parts = []
    for key, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n"
        )
    parts.append(f"--{boundary}--\r\n")
    return "".join(parts).encode()


def pypi_upload_probe(
    username: str,
    password: str,
    *,
    package: str | None = None,
    version: str | None = None,
    test: bool = False,
    timeout: int = 10,
) -> PyPIProbe:
    """Ask the upload endpoint what it thinks of this upload, right now.

    Publishes nothing: the request carries ``:action=file_upload`` and no
    distribution file, so the request is refused before any artifact can be
    stored, and the refusal is the answer we are after.

    Without *package* only the credentials are exercised (this is what
    ``check:pypi`` needs). With *package* and *version* the form carries the
    project metadata, so PyPI resolves the project name before it ever looks
    for the file, and the answer covers the two failures a nameless probe is
    structurally blind to:

      * creating a NEW project is rate limited separately, keyed on the project
        name, and answers ``429``
      * a project-scoped token that is not scoped to THIS project answers ``403``

    Status codes:

      * ``400`` accepted this far, refused only for the missing file
      * ``401`` / ``403`` credentials rejected, or not allowed for this project
      * ``429`` the endpoint is rate limiting this account or this project name

    Raises :class:`RegistryError` when PyPI cannot be reached at all, so a dead
    network is never mistaken for a verdict.
    """
    upload_url = TEST_PYPI_UPLOAD_URL if test else PYPI_UPLOAD_URL
    boundary = "splentclicheck"

    fields = {":action": "file_upload", "protocol_version": "1"}
    named = bool(package)
    if named:
        # Enough valid metadata for the upload form to validate, so the request
        # reaches the project name resolution instead of being turned away for a
        # malformed form. No file is attached, so nothing can be published.
        fields.update(
            {
                "metadata_version": "2.1",
                "name": package,
                "version": (version or "0.0.0").lstrip("v"),
                "filetype": "sdist",
                "pyversion": "source",
                "md5_digest": "0" * 32,
            }
        )

    body = _multipart_body(fields, boundary)
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()

    req = urllib.request.Request(
        upload_url,
        data=body,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return PyPIProbe(status=_status_of(resp), rate_limited=False, named=named)
    except urllib.error.HTTPError as e:
        headers = e.headers or {}
        retry_after = headers.get("Retry-After") if hasattr(headers, "get") else None
        try:
            detail = (e.read() or b"").decode("utf-8", errors="replace")[:200].strip()
        except Exception:
            detail = ""
        return PyPIProbe(
            status=e.code,
            rate_limited=e.code == 429,
            retry_after=retry_after,
            detail=detail,
            named=named,
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise RegistryError(f"Network error: {getattr(e, 'reason', e)}")


def pypi_project_exists(package: str, timeout: int = 10) -> bool:
    """Whether *package* is already a project on PyPI.

    ``False`` means this upload would CREATE the project, which PyPI rate limits
    far more strictly than a new version of an existing project. Raises
    :class:`RegistryError` (``rate_limited`` set on 429) rather than guessing,
    because "unknown" and "new project" must never collapse into one answer.
    """
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= _status_of(resp) < 300
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise RegistryError(
            f"PyPI API error (HTTP {e.code})",
            status=e.code,
            rate_limited=e.code == 429,
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise RegistryError(f"Network error: {getattr(e, 'reason', e)}")


def pypi_versions(package: str, *, strict: bool = False) -> list[str]:
    """All PyPI release versions of *package*, newest upload first.

    Quiet ``[]`` on any failure by default, which is what the historical call
    sites expect. With ``strict=True`` only a 404 (the project does not exist)
    answers ``[]``; every other failure raises :class:`RegistryError`, so a 429
    or a dead network is never rendered as "nothing is published".
    """
    url = f"https://pypi.org/pypi/{package}/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404 or not strict:
            return []
        raise RegistryError(
            f"PyPI API error (HTTP {e.code})",
            status=e.code,
            rate_limited=e.code == 429,
        )
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        if strict:
            raise RegistryError(f"PyPI could not be read ({getattr(e, 'reason', e)})")
        return []
    releases = data.get("releases", {})

    def _latest_upload(v):
        files = releases.get(v) or []
        if not files:
            return ""
        return max(f.get("upload_time", "") for f in files)

    return sorted(releases.keys(), key=_latest_upload, reverse=True)


DOCKERHUB_API = "https://hub.docker.com/v2"


@dataclasses.dataclass(frozen=True)
class DockerProbe:
    """Outcome of a credential probe against Docker Hub."""

    status: int
    rate_limited: bool
    detail: str = ""


def dockerhub_login_probe(
    username: str, password: str, timeout: int = 10
) -> DockerProbe:
    """Ask Docker Hub whether these credentials are usable, right now.

    Pushes nothing: it only exchanges the credentials for a token. ``200`` means
    the login works, ``401`` means it does not, ``429`` means Docker Hub is
    rate limiting, which is not a credential problem.
    """
    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{DOCKERHUB_API}/users/login/",
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return DockerProbe(status=_status_of(resp), rate_limited=False)
    except urllib.error.HTTPError as e:
        try:
            detail = (e.read() or b"").decode("utf-8", errors="replace")[:200].strip()
        except Exception:
            detail = ""
        return DockerProbe(status=e.code, rate_limited=e.code == 429, detail=detail)
    except (urllib.error.URLError, TimeoutError) as e:
        raise RegistryError(f"Network error: {getattr(e, 'reason', e)}")


def dockerhub_tag_exists(
    namespace: str, repo: str, tag: str, timeout: int = 10
) -> bool:
    """Whether ``namespace/repo:tag`` is already on Docker Hub.

    Raises :class:`RegistryError` rather than guessing, so "not published" is
    never invented out of a failed read.
    """
    url = f"{DOCKERHUB_API}/repositories/{namespace}/{repo}/tags/{tag}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= _status_of(resp) < 300
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise RegistryError(
            f"Docker Hub API error (HTTP {e.code})",
            status=e.code,
            rate_limited=e.code == 429,
        )
    except (urllib.error.URLError, TimeoutError) as e:
        raise RegistryError(f"Network error: {getattr(e, 'reason', e)}")


def pypi_version_exists(package: str, version: str, *, strict: bool = False) -> bool:
    """Check one version against the per-version endpoint (not CDN-cached).

    Quiet by default (the historical behavior every call site relies on): any
    non-404 error counts as "exists" and a dead network counts as "does not".
    With ``strict=True`` the ambiguity is raised as a :class:`RegistryError`
    instead — a 429 must never be reported as "already published".
    """
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        if strict:
            raise RegistryError(
                f"PyPI API error (HTTP {e.code})",
                status=e.code,
                rate_limited=e.code == 429,
            )
        return True
    except (urllib.error.URLError, TimeoutError) as e:
        if strict:
            raise RegistryError(f"Network error: {getattr(e, 'reason', e)}")
        return False
