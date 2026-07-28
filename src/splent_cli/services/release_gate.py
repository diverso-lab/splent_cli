"""The release gate: prove every channel accepts a publication BEFORE mutating.

A release publishes to GitHub and PyPI, and a product also publishes a Docker
image. A refusal discovered halfway through leaves those channels permanently
out of step: a tag that no package on PyPI corresponds to, or a version on PyPI
whose source nobody can find.

This module answers, for one specific repository, package version and image,
the only question that prevents that:

    will GitHub accept a push, a tag and a release, will PyPI accept this
    upload, and will Docker Hub accept this image, right now?

Every answer is one of three things and never anything else:

    ready     verified, go ahead
    blocked   a refusal, with the channel named and the cause separated into
              "credentials" versus "rate limit" (a 429 is never reported as a
              credential problem, and a 401 is never reported as a rate limit)
    skipped   the operator deliberately took this channel out of the release,
              and the pipeline will then publish NOTHING there

Doubt counts as blocked. A false green costs a permanent divergence; a false
red costs waiting.

The PyPI check names the project it is about, so the answer covers the two
refusals a nameless credential probe is structurally blind to: the rate limit
on creating a NEW project, which is keyed on the project name, and a
project-scoped token that is not scoped to this project.

All network access goes through :mod:`splent_cli.services.registry`, the single
GitHub, PyPI and Docker Hub boundary. Nothing here publishes anything.
"""

from __future__ import annotations

import dataclasses
import os
import time
import tomllib

import click

from splent_cli.services import registry


GITHUB = "github"
PYPI = "pypi"
DOCKER = "docker"

# Every channel the pipeline knows how to publish to, in the order they are
# reported. Docker Hub only applies to products, so it is never a default.
ALL_CHANNELS: tuple[str, ...] = (GITHUB, PYPI, DOCKER)
DEFAULT_CHANNELS: tuple[str, ...] = (GITHUB, PYPI)

_LABELS = {GITHUB: "github", PYPI: "pypi", DOCKER: "docker"}

_TOKENS_HINT = "Run 'splent tokens:setup' for instructions on obtaining tokens."


class ChannelDeclarationError(Exception):
    """``[tool.splent.release]`` says something the gate cannot act on.

    Never resolved by falling back to a default. Publishing to more channels
    than the author asked for is the unrecoverable direction: a PyPI version
    cannot be deleted and re-uploaded.
    """


# ── Results ───────────────────────────────────────────────────────────


@dataclasses.dataclass
class ChannelStatus:
    """What the gate established about one channel.

    ``ok`` means verified-and-go. ``skipped`` means deliberately excluded. Any
    other combination is a refusal, whatever the reason.

    Channel specific facts are carried so callers (notably ``release:resume``)
    do not have to ask the same questions a second time:

    github
        ``tag_exists``       ``refs/tags/<tag>`` is already on the remote
        ``already_published`` a GitHub Release already exists for the tag
    pypi
        ``project_exists``   the project already exists on PyPI
        ``new_project``      this upload would create the project
        ``already_published`` this exact version is already on PyPI
    """

    channel: str
    ok: bool = False
    skipped: bool = False
    rate_limited: bool = False
    unproven: bool = False
    summary: str = ""
    hints: list[str] = dataclasses.field(default_factory=list)

    tag_exists: bool = False
    latest_tag: str | None = None
    project_exists: bool = False
    new_project: bool = False
    already_published: bool = False

    @property
    def blocked(self) -> bool:
        return not self.ok and not self.skipped

    @property
    def label(self) -> str:
        return _LABELS.get(self.channel, self.channel)


@dataclasses.dataclass
class GateReport:
    """The verdict for every channel of one release."""

    statuses: list[ChannelStatus] = dataclasses.field(default_factory=list)
    # The previous version never finished publishing. Releasing a new number on
    # top of it burns the number and leaves the old one broken, so it is a
    # refusal unless the operator says otherwise out loud.
    unfinished: "IncompleteRelease | None" = None
    unfinished_allowed: bool = False

    def of(self, channel: str) -> ChannelStatus | None:
        for st in self.statuses:
            if st.channel == channel:
                return st
        return None

    @property
    def github(self) -> ChannelStatus | None:
        return self.of(GITHUB)

    @property
    def pypi(self) -> ChannelStatus | None:
        return self.of(PYPI)

    @property
    def docker(self) -> ChannelStatus | None:
        return self.of(DOCKER)

    @property
    def ok(self) -> bool:
        """True only when no channel is blocked. An empty gate is never ok."""
        if not self.statuses:
            return False
        if self.unfinished is not None and not self.unfinished_allowed:
            return False
        return not any(st.blocked for st in self.statuses)

    @property
    def blocked(self) -> list[ChannelStatus]:
        return [st for st in self.statuses if st.blocked]

    @property
    def skipped(self) -> list[ChannelStatus]:
        return [st for st in self.statuses if st.skipped]

    @property
    def rate_limited(self) -> list[ChannelStatus]:
        return [st for st in self.statuses if st.rate_limited]

    @property
    def unproven(self) -> list[ChannelStatus]:
        return [st for st in self.statuses if st.ok and st.unproven]


# ── Declared channels ─────────────────────────────────────────────────


def normalize_channels(channels) -> tuple[str, ...]:
    """Keep only known channel names, in canonical order, without duplicates."""
    if channels is None:
        return DEFAULT_CHANNELS
    wanted = {str(c).strip().lower() for c in channels}
    return tuple(c for c in ALL_CHANNELS if c in wanted)


_RELEASE_KEYS = {"channels"}


def declared_channels(pyproject_path: str) -> tuple[str, ...]:
    """Channels this package publishes to, from ``[tool.splent.release]``.

    A package that never goes to PyPI (a private feature, for instance) declares
    it once in its own pyproject::

        [tool.splent.release]
        channels = ["github"]

    Silence means the default (GitHub and PyPI). Anything that is present but
    unusable raises :class:`ChannelDeclarationError` instead of falling back:
    a misspelled channel name or a misspelled key would otherwise publish to
    PyPI a package whose author asked for GitHub only, and that cannot be undone.
    """
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_CHANNELS

    tool = data.get("tool")
    splent = tool.get("splent") if isinstance(tool, dict) else None
    section = splent.get("release") if isinstance(splent, dict) else None
    if section is None:
        return DEFAULT_CHANNELS
    if not isinstance(section, dict):
        raise ChannelDeclarationError(
            "[tool.splent.release] must be a table, not " + type(section).__name__
        )

    unknown = sorted(set(section) - _RELEASE_KEYS)
    if unknown:
        raise ChannelDeclarationError(
            "[tool.splent.release] has unknown key(s) "
            + ", ".join(unknown)
            + ". The only key is 'channels', so this was ignored silently before."
        )

    if "channels" not in section:
        return DEFAULT_CHANNELS
    raw = section["channels"]
    if not isinstance(raw, list):
        raise ChannelDeclarationError(
            "[tool.splent.release] channels must be a list, for example "
            'channels = ["github"]'
        )
    if not raw:
        raise ChannelDeclarationError(
            "[tool.splent.release] channels is empty, so this package would "
            "publish nowhere. Remove the key to publish to the default channels."
        )
    normalized = normalize_channels(raw)
    unrecognized = sorted({str(c).strip().lower() for c in raw} - set(ALL_CHANNELS))
    if unrecognized:
        raise ChannelDeclarationError(
            "[tool.splent.release] channels names an unknown channel: "
            + ", ".join(unrecognized)
            + ". Known channels are "
            + ", ".join(ALL_CHANNELS)
            + "."
        )
    return normalized


# ── Credentials, resolved exactly once ────────────────────────────────


def pypi_credentials() -> tuple[str | None, str | None]:
    """The PyPI credentials the upload will actually use.

    twine reads ``TWINE_USERNAME`` and ``TWINE_PASSWORD`` from the environment
    and nothing else. ``PYPI_USERNAME`` / ``PYPI_PASSWORD`` are accepted here as
    a convenience, and :func:`splent_cli.services.release.twine_env` puts the
    resolved values under the names twine reads, so the gate can never verify
    one credential while the upload runs with another.
    """
    username = os.getenv("TWINE_USERNAME") or os.getenv("PYPI_USERNAME") or None
    password = os.getenv("TWINE_PASSWORD") or os.getenv("PYPI_PASSWORD") or None
    return username, password


def docker_credentials() -> tuple[str | None, str | None]:
    """The Docker Hub credentials the image push will use."""
    return os.getenv("DOCKERHUB_USERNAME") or None, os.getenv(
        "DOCKERHUB_PASSWORD"
    ) or None


# ── Rate limit windows, reported without inventing numbers ────────────


def github_window(reset_epoch) -> str:
    """What is actually known about a GitHub rate-limit window."""
    try:
        reset = int(reset_epoch)
    except (TypeError, ValueError):
        return "GitHub did not report when this window resets."
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(reset))
    remaining = reset - int(time.time())
    if remaining <= 0:
        return f"The window rolled over at {stamp}, so a retry should work now."
    minutes = (remaining + 59) // 60
    return f"The window resets at {stamp}, about {minutes} min from now."


def pypi_window(retry_after: str | None) -> str:
    """What is actually known about a PyPI rate-limit window.

    PyPI publishes no reset time for its upload limits, so there is nothing to
    report beyond a ``Retry-After`` header when it sends one. Saying that is
    correct; naming a duration would be a guess.
    """
    if retry_after:
        return f"PyPI asked for a retry after {retry_after}."
    return (
        "PyPI publishes no reset time for this limit, so the window is unknown. "
        "Creating a NEW project is limited far more strictly than uploading a "
        "new version of an existing one."
    )


# ── GitHub channel ────────────────────────────────────────────────────


def semver_key(version: str | None) -> tuple[int, int, int] | None:
    """``(major, minor, patch)`` of a version or tag, or ``None`` if unparsable."""
    if not version:
        return None
    m = registry.SEMVER_RE.match(str(version).strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _is_older(candidate: str, existing: str | None) -> bool:
    """Whether *candidate* is a lower version than *existing*."""
    a, b = semver_key(candidate), semver_key(existing)
    if a is None or b is None:
        return False
    return a < b


def _github_error(
    st: ChannelStatus, e: registry.RegistryError, what: str
) -> ChannelStatus:
    """Translate a registry failure into a refusal, keeping the causes apart."""
    if e.rate_limited or e.status == 429:
        st.rate_limited = True
        st.summary = f"blocked, GitHub is rate limiting this token (HTTP {e.status})"
        st.hints.append(github_window(None))
        st.hints.append("This is a rate limit, not a credential problem.")
    elif e.status == 401:
        st.summary = "blocked, GITHUB_TOKEN was rejected (HTTP 401)"
        st.hints.append("The token is invalid, expired or revoked. " + _TOKENS_HINT)
    elif e.status == 403:
        st.summary = f"blocked, GITHUB_TOKEN is not allowed to {what} (HTTP 403)"
        st.hints.append("The token needs the 'repo' scope for this repository.")
    elif e.status is None:
        st.summary = f"blocked, GitHub could not be reached ({e})"
    else:
        st.summary = f"blocked, GitHub answered HTTP {e.status} when asked to {what}"
    return st


def check_github(
    repo: str,
    tag: str,
    *,
    token: str | None,
    resume: bool = False,
) -> ChannelStatus:
    """Establish that *repo* will accept a push, the tag *tag*, and a release."""
    st = ChannelStatus(GITHUB)

    if not token:
        st.summary = "blocked, GITHUB_TOKEN is not set"
        st.hints.append(
            "Without it a push, a tag and a release cannot be verified. " + _TOKENS_HINT
        )
        st.hints.append(
            "To publish to PyPI only, re-run with --no-github (explicit, one channel)."
        )
        return st

    if "/" not in repo:
        st.summary = f"blocked, '{repo}' is not an org/repo reference"
        return st
    org, name = repo.split("/", 1)

    # Budget first. Asking for it costs nothing, and an exhausted budget is the
    # one failure that would otherwise masquerade as a permission problem.
    try:
        limit = registry.github_rate_limit(token)
    except registry.RegistryError as e:
        return _github_error(st, e, "read the rate limit")
    if limit and limit.get("remaining") == 0:
        st.rate_limited = True
        st.summary = "blocked, the GitHub API budget for this token is exhausted"
        st.hints.append(github_window(limit.get("reset")))
        st.hints.append("This is a rate limit, not a credential problem.")
        return st

    try:
        user = registry.github_user(token)
    except registry.RegistryError as e:
        return _github_error(st, e, "identify the token")
    login = (user or {}).get("login") or "unknown"

    try:
        data = registry.fetch_repo(org, name, token)
    except registry.RegistryError as e:
        return _github_error(st, e, f"read {repo}")
    if data is None:
        st.summary = f"blocked, {repo} does not exist or this token cannot see it"
        st.hints.append(
            f"Authenticated as {login}. Check the repository name and the token scope."
        )
        return st

    if data.get("archived"):
        st.summary = f"blocked, {repo} is archived"
        st.hints.append(
            "An archived repository rejects pushes, tags and releases. "
            "Unarchive it in the repository settings first."
        )
        return st

    permissions = data.get("permissions") or {}
    if not permissions.get("push", False):
        st.summary = f"blocked, {login} cannot push to {repo}"
        st.hints.append(
            "A tag and a release both need write access to this repository, "
            "not just to the account. " + _TOKENS_HINT
        )
        return st

    # Tag and release state. For a fresh release either one already existing
    # means this version was released before, which is the incomplete-release
    # case, not a new one.
    try:
        st.tag_exists = registry.github_tag_exists(org, name, tag, token)
    except registry.RegistryError as e:
        return _github_error(st, e, f"read the tags of {repo}")

    # A version LOWER than what is already tagged is a regression, and a tag can
    # never be taken back. This is the failure a wizard that could not read the
    # tag list produces: it offers v0.0.1 for a repo that is already at v0.2.0.
    if not resume:
        try:
            st.latest_tag = registry.latest_semver_tag(org, name, token, strict=True)
        except registry.RegistryError as e:
            return _github_error(st, e, f"read the latest tag of {repo}")
        if _is_older(tag, st.latest_tag):
            st.summary = (
                f"blocked, {tag} is older than {st.latest_tag}, "
                f"which is already tagged on {repo}"
            )
            st.hints.append(
                "A tag cannot be taken back, so a version that goes backwards is "
                "permanent. Release a version above " + str(st.latest_tag) + "."
            )
            return st

    if st.tag_exists:
        try:
            release_data = registry.github_release_by_tag(org, name, tag, token)
        except registry.RegistryError as e:
            return _github_error(st, e, f"read the releases of {repo}")
        st.already_published = release_data is not None

    if st.tag_exists and not resume:
        st.summary = f"blocked, {tag} is already tagged on {repo}"
        st.hints.append(
            "That version was released before. Finish it with "
            "'splent release:resume' instead of releasing a new version, so no "
            "version number is burned."
        )
        return st

    st.ok = True
    if resume and st.tag_exists:
        done = "release already created" if st.already_published else "release missing"
        st.summary = f"ready, {tag} is tagged on {repo}, {done}"
    else:
        st.summary = f"ready, {repo} accepts push, tag and release as {login}"
    return st


# ── PyPI channel ──────────────────────────────────────────────────────


def check_pypi(
    package: str,
    version: str,
    *,
    username: str | None,
    password: str | None,
    resume: bool = False,
    deep: bool = True,
) -> ChannelStatus:
    """Establish that PyPI will accept *package* *version* right now.

    With ``deep`` (the default) the probe carries the project name and version,
    so PyPI resolves the project before it looks for the missing file. That is
    what makes the two failures that actually happen visible in advance: the
    project-CREATION rate limit, which is keyed on the name and is far stricter
    than the limit on a new version of an existing project, and a project-scoped
    token that is not scoped to this project.
    """
    st = ChannelStatus(PYPI)
    normalized = version.lstrip("v")

    if not username or not password:
        missing = []
        if not username:
            missing.append("TWINE_USERNAME or PYPI_USERNAME")
        if not password:
            missing.append("TWINE_PASSWORD or PYPI_PASSWORD")
        st.summary = "blocked, PyPI credentials are not set"
        st.hints.append("Missing " + ", ".join(missing) + ". " + _TOKENS_HINT)
        st.hints.append(
            "To publish to GitHub only, re-run with --no-pypi (explicit, one channel)."
        )
        return st

    # 1. Does the project exist? A first upload CREATES the project, which PyPI
    #    limits far more strictly than a new version of an existing project.
    #    Asked first because it is what the probe below has to be told about.
    try:
        st.project_exists = registry.pypi_project_exists(package)
    except registry.RegistryError as e:
        if e.rate_limited:
            st.rate_limited = True
            st.summary = f"blocked, PyPI is rate limiting reads of {package} (HTTP 429)"
            st.hints.append(pypi_window(None))
            st.hints.append("This is a rate limit, not a credential problem.")
        else:
            st.summary = f"blocked, PyPI would not say whether {package} exists ({e})"
        return st
    st.new_project = not st.project_exists

    # 2. Will this upload be accepted? The probe posts an upload with no file,
    #    so it publishes nothing, and names the project so the answer covers the
    #    project-creation limiter and the per-project permission.
    try:
        probe = registry.pypi_upload_probe(
            username,
            password,
            package=package if deep else None,
            version=normalized if deep else None,
        )
    except registry.RegistryError as e:
        st.summary = f"blocked, PyPI could not be reached ({e})"
        return st

    if probe.rate_limited:
        st.rate_limited = True
        if probe.named and st.new_project:
            st.summary = (
                f"blocked, PyPI is rate limiting the creation of new projects "
                f"(HTTP 429), and {package} would be a new project"
            )
        else:
            st.summary = "blocked, PyPI is rate limiting this account (HTTP 429)"
        st.hints.append(pypi_window(probe.retry_after))
        st.hints.append("This is a rate limit, not a credential problem.")
        return st

    if probe.status == 403 and probe.named:
        st.summary = (
            f"blocked, this PyPI token is not allowed to upload {package} (HTTP 403)"
        )
        st.hints.append(
            "A project-scoped token only works for the project it was issued for. "
            + _TOKENS_HINT
        )
        if probe.detail:
            st.hints.append(probe.detail)
        return st

    if probe.status in (401, 403):
        st.summary = f"blocked, PyPI rejected the credentials (HTTP {probe.status})"
        st.hints.append(
            "The token is invalid, expired or out of scope. " + _TOKENS_HINT
        )
        return st

    if probe.status not in (200, 400):
        # 400 is the expected answer to a fileless upload from a valid token.
        # Anything else is not a verdict, and an unknown verdict is a refusal.
        st.summary = (
            f"blocked, PyPI answered HTTP {probe.status} to the credential probe, "
            "which is not a usable answer"
        )
        if probe.detail:
            st.hints.append(probe.detail)
        st.hints.append("Retry once PyPI answers normally. Nothing was published.")
        return st

    # 3. Is this exact version already there? PyPI never accepts a version twice.
    _pypi_version_state(st, package, normalized)
    if st.summary:
        return st

    if st.already_published and not resume:
        st.summary = f"blocked, {package} {normalized} is already on PyPI"
        st.hints.append(
            "PyPI never accepts the same version twice. Release a higher version, "
            "or finish an interrupted release with 'splent release:resume'."
        )
        return st

    st.ok = True
    if st.already_published:
        st.summary = (
            f"ready, {package} {normalized} is already on PyPI, nothing to upload"
        )
    elif st.new_project:
        if probe.named:
            st.summary = (
                f"ready, this upload would create {package} and PyPI accepted the "
                "project name and the credentials"
            )
        else:
            # A nameless probe cannot exercise the creation limiter, which is
            # keyed on the project name. Say so rather than implying a proof.
            st.unproven = True
            st.summary = (
                f"ready, credentials accepted, {package} does not exist yet so this "
                "upload would create the project"
            )
            st.hints.append(
                "Project creation is rate limited separately and was not verified "
                "for this name."
            )
    else:
        st.summary = (
            f"ready, credentials accepted, {package} exists so this is a new version"
        )
    return st


def _pypi_version_state(st: ChannelStatus, package: str, normalized: str) -> None:
    """Fill ``already_published``, or set a refusal summary when it is unknown.

    "PyPI would not answer" and "this version is not published" are different
    facts, and collapsing them is how a release gets attempted for a version
    that is already out there.
    """
    if not st.project_exists:
        return
    try:
        st.already_published = registry.pypi_version_exists(
            package, normalized, strict=True
        )
    except registry.RegistryError as e:
        if e.rate_limited:
            st.rate_limited = True
            st.summary = f"blocked, PyPI is rate limiting reads of {package} (HTTP 429)"
            st.hints.append(pypi_window(None))
            st.hints.append("This is a rate limit, not a credential problem.")
        else:
            st.summary = (
                f"blocked, PyPI would not say whether {package} {normalized} "
                f"is published ({e})"
            )


# ── Docker Hub channel ─────────────────────────────────────────────────


def check_docker(
    image: str | None,
    version: str,
    *,
    username: str | None,
    password: str | None,
    resume: bool = False,
) -> ChannelStatus:
    """Establish that Docker Hub will accept this image push.

    A product release publishes an image too, and it publishes it LAST, after
    GitHub and PyPI are already irreversible. That makes it the channel most
    able to diverge, so it is verified with the others, before anything moves.
    """
    st = ChannelStatus(DOCKER)
    normalized = version.lstrip("v")

    if not username or not password:
        missing = [
            name
            for name, value in (
                ("DOCKERHUB_USERNAME", username),
                ("DOCKERHUB_PASSWORD", password),
            )
            if not value
        ]
        st.summary = "blocked, Docker Hub credentials are not set"
        st.hints.append("Missing " + ", ".join(missing) + ". " + _TOKENS_HINT)
        return st

    try:
        probe = registry.dockerhub_login_probe(username, password)
    except registry.RegistryError as e:
        st.summary = f"blocked, Docker Hub could not be reached ({e})"
        return st

    if probe.rate_limited:
        st.rate_limited = True
        st.summary = "blocked, Docker Hub is rate limiting this account (HTTP 429)"
        st.hints.append(
            "Docker Hub publishes no reset time for this limit, so the window is "
            "unknown."
        )
        st.hints.append("This is a rate limit, not a credential problem.")
        return st

    if probe.status in (401, 403):
        st.summary = (
            f"blocked, Docker Hub rejected the credentials (HTTP {probe.status})"
        )
        st.hints.append(
            "DOCKERHUB_USERNAME / DOCKERHUB_PASSWORD are invalid or expired. "
            + _TOKENS_HINT
        )
        return st

    if not (200 <= probe.status < 300):
        st.summary = (
            f"blocked, Docker Hub answered HTTP {probe.status} to the credential "
            "probe, which is not a usable answer"
        )
        if probe.detail:
            st.hints.append(probe.detail)
        return st

    if image and "/" in image:
        namespace, repository = image.split("/", 1)
        try:
            st.already_published = registry.dockerhub_tag_exists(
                namespace, repository, normalized
            )
        except registry.RegistryError as e:
            if e.rate_limited:
                st.rate_limited = True
                st.summary = (
                    f"blocked, Docker Hub is rate limiting reads of {image} (HTTP 429)"
                )
                st.hints.append("This is a rate limit, not a credential problem.")
            else:
                st.summary = (
                    f"blocked, Docker Hub would not say whether {image}:{normalized} "
                    f"exists ({e})"
                )
            return st

    st.ok = True
    if st.already_published:
        st.summary = f"ready, {image}:{normalized} is already pushed"
    else:
        st.summary = f"ready, credentials accepted for {image or 'Docker Hub'}"
    return st


# ── The gate ──────────────────────────────────────────────────────────


def run_gate(
    *,
    repo: str,
    package: str,
    version: str,
    channels=None,
    resume: bool = False,
    token: str | None = None,
    pypi_username: str | None = None,
    pypi_password: str | None = None,
    docker_image: str | None = None,
    docker_username: str | None = None,
    docker_password: str | None = None,
    deep: bool = True,
    allow_unfinished: bool = False,
) -> GateReport:
    """Check every enabled channel. Channels left out are reported as skipped.

    Channels that are off are reported as skipped and are NOT checked, which is
    only sound because the pipeline does not touch a channel it did not check.
    """
    enabled = normalize_channels(channels)
    tag = version if version.startswith("v") else f"v{version}"

    if token is None:
        token = os.getenv("GITHUB_TOKEN")
    if pypi_username is None or pypi_password is None:
        env_user, env_password = pypi_credentials()
        pypi_username = pypi_username or env_user
        pypi_password = pypi_password or env_password
    if docker_username is None or docker_password is None:
        env_user, env_password = docker_credentials()
        docker_username = docker_username or env_user
        docker_password = docker_password or env_password

    # Channels nobody asked about are not reported at all. Docker only applies
    # to products, so a feature release must not announce that it is "off".
    considered = tuple(
        c for c in ALL_CHANNELS if c in set(DEFAULT_CHANNELS) | set(enabled)
    )

    report = GateReport(unfinished_allowed=allow_unfinished)
    for channel in considered:
        if channel not in enabled:
            skipped = ChannelStatus(channel, skipped=True)
            skipped.summary = "SKIPPED on purpose, this release does not publish here"
            report.statuses.append(skipped)
            continue
        if channel == GITHUB:
            report.statuses.append(check_github(repo, tag, token=token, resume=resume))
        elif channel == PYPI:
            report.statuses.append(
                check_pypi(
                    package,
                    version,
                    username=pypi_username,
                    password=pypi_password,
                    resume=resume,
                    deep=deep,
                )
            )
        else:
            report.statuses.append(
                check_docker(
                    docker_image,
                    version,
                    username=docker_username,
                    password=docker_password,
                    resume=resume,
                )
            )

    # Bumping past a release that never finished is how version numbers get
    # burned. It is checked here, in the gate, so it applies to every path,
    # including the one that passes the version on the command line and never
    # opens the wizard.
    if not resume and GITHUB in enabled and (report.github and report.github.ok):
        try:
            report.unfinished = find_incomplete_release(
                repo,
                package,
                channels=enabled,
                token=token,
                strict=True,
                skip_tag=tag,
            )
        except registry.RegistryError as e:
            st = report.github
            st.ok = False
            st.summary = (
                f"blocked, could not tell whether the previous release finished ({e})"
            )
            st.hints.append(
                "Releasing on top of an unfinished release burns a version number, "
                "so an unknown answer is a refusal here."
            )
    return report


# ── Reporting ─────────────────────────────────────────────────────────


def render(report: GateReport, *, header: bool = True) -> None:
    """Print the gate result in the same shape as the rest of the pipeline."""
    if header:
        click.echo()
        click.secho("  Release gate (channels)", bold=True)

    for st in report.statuses:
        if st.skipped:
            color = "yellow"
        elif st.blocked:
            color = "red"
        elif st.unproven:
            color = "yellow"
        else:
            color = "green"
        click.secho(f"  {st.label:<9}{st.summary}", fg=color)
        for hint in st.hints:
            click.secho(f"           {hint}", fg="bright_black")

    if report.unfinished:
        click.echo()
        color = "yellow" if report.unfinished_allowed else "red"
        click.secho(f"  previous {report.unfinished.description}.", fg=color, bold=True)
        if report.unfinished_allowed:
            click.secho(
                "           Released anyway on purpose (--allow-unfinished). The "
                "older version stays broken.",
                fg="yellow",
            )

    if report.skipped:
        names = ", ".join(st.label for st in report.skipped)
        click.echo()
        click.secho(
            f"  WARNING  {names} is turned off for this release, so nothing at all "
            "is published there.",
            fg="yellow",
            bold=True,
        )
        click.secho(
            "           The channels will not match after it. That is the point of "
            "the flag, but it is now on you to remember.",
            fg="yellow",
        )


def refuse(report: GateReport, *, resume_target: str | None = None) -> None:
    """Print why the gate refused and exit non zero. Nothing has been mutated."""
    click.echo()

    if report.unfinished and not report.unfinished_allowed:
        click.secho(
            f"  error: {report.unfinished.description}.",
            fg="red",
            bold=True,
        )
        click.secho(
            "  Releasing a new version now would burn a version number and leave "
            "the old one broken. Finish it first with",
            fg="yellow",
        )
        click.echo(f"    splent release:resume {resume_target or '<name>'}")
        click.secho(
            "  If that version is never going to be finished, re-run with "
            "--allow-unfinished.",
            fg="bright_black",
        )
        click.echo()

    blocked = report.blocked
    if blocked:
        names = ", ".join(st.label for st in blocked)
        click.secho(
            f"  error: release refused, {names} will not accept it.",
            fg="red",
            bold=True,
        )
        # Each blocked channel is named with its own cause. A 429 on one channel
        # says nothing about a 401 on another, and a summary that mixes them
        # sends the operator to wait for a window that will never fix a token.
        for st in blocked:
            click.secho(f"    {st.label:<9}{st.summary}", fg="red")
        limited = [st.label for st in blocked if st.rate_limited]
        credentials = [st.label for st in blocked if not st.rate_limited]
        if limited:
            click.secho(
                f"  {', '.join(limited)} is RATE LIMITED. Those credentials are "
                "fine, the channel is not accepting publications right now.",
                fg="yellow",
            )
        if limited and credentials:
            click.secho(
                f"  {', '.join(credentials)} is a different problem and waiting "
                "will not fix it. Read the line above.",
                fg="yellow",
            )

    click.secho(
        "  Nothing was changed. No version bump, no commit, no tag, no release.",
        fg="bright_black",
    )
    click.echo()
    raise SystemExit(1)


# ── Incomplete releases ───────────────────────────────────────────────


@dataclasses.dataclass
class IncompleteRelease:
    """A version that reached one channel and not the other."""

    tag: str
    missing: list[str] = dataclasses.field(default_factory=list)

    @property
    def description(self) -> str:
        return f"{self.tag} is tagged but missing from " + " and ".join(self.missing)


def find_incomplete_release(
    repo: str,
    package: str,
    *,
    channels=None,
    token: str | None = None,
    strict: bool = False,
    skip_tag: str | None = None,
) -> IncompleteRelease | None:
    """The newest tag that never finished publishing, or ``None``.

    This is what tells an interrupted release apart from a fresh one, so a rerun
    can finish the job instead of bumping the version again and burning a number.

    Quiet by default. With ``strict=True`` a failed lookup raises instead of
    answering "nothing is wrong", because a silent no-op here is exactly how a
    version gets burned while GitHub is answering 401.
    """
    enabled = normalize_channels(channels)
    if "/" not in repo:
        return None
    org, name = repo.split("/", 1)
    if token is None:
        token = os.getenv("GITHUB_TOKEN")

    try:
        tag = registry.latest_semver_tag(org, name, token, strict=strict)
    except registry.RegistryError:
        if strict:
            raise
        return None
    if not tag or (skip_tag and tag.lstrip("v") == skip_tag.lstrip("v")):
        return None

    missing: list[str] = []
    try:
        if PYPI in enabled and not registry.pypi_version_exists(
            package, tag.lstrip("v"), strict=True
        ):
            missing.append("PyPI")
        if (
            GITHUB in enabled
            and registry.github_release_by_tag(org, name, tag, token) is None
        ):
            missing.append("the GitHub release")
    except registry.RegistryError:
        if strict:
            raise
        return None

    if not missing:
        return None
    return IncompleteRelease(tag=tag, missing=missing)
