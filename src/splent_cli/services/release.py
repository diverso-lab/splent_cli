"""
Shared release pipeline helpers.

Used by feature:release, product:release, release:cli, and release:framework.
All release commands delegate to these functions instead of reimplementing
git, PyPI, or GitHub logic.
"""

import os
import re
import sys
import subprocess
import tomllib

import click

from splent_cli.services import release_gate
from splent_cli.utils.proc import require_tool, run


# ── Environment validation ────────────────────────────────────────────


def validate_release_env(
    *,
    require_pypi: bool = True,
    require_docker: bool = False,
    require_github: bool = True,
):
    """Check that the required credentials are present in the environment.

    Presence only. Whether the credentials WORK, and whether either channel is
    accepting publications at all, is the release gate's job
    (:mod:`splent_cli.services.release_gate`), which runs before the first
    mutation and refuses instead of warning.
    """
    missing = []

    if require_github and not os.getenv("GITHUB_TOKEN"):
        click.secho(
            "  warn: GITHUB_TOKEN not set, the release gate will refuse the "
            "GitHub channel",
            fg="yellow",
        )

    if require_pypi:
        username, password = release_gate.pypi_credentials()
        if not username:
            missing.append("TWINE_USERNAME or PYPI_USERNAME")
        if not password:
            missing.append("TWINE_PASSWORD or PYPI_PASSWORD")

    if require_docker:
        if not os.getenv("DOCKERHUB_USERNAME"):
            missing.append("DOCKERHUB_USERNAME")
        if not os.getenv("DOCKERHUB_PASSWORD"):
            missing.append("DOCKERHUB_PASSWORD")

    if missing:
        click.secho("  Missing required environment variables:", fg="red")
        for m in missing:
            click.echo(f"    - {m}")
        click.echo()
        click.secho(
            "  Run 'splent tokens:setup' for instructions on how to obtain them.",
            fg="bright_black",
        )
        raise SystemExit(1)


# ── Git helpers ───────────────────────────────────────────────────────


def extract_repo(remote_url: str) -> str:
    """Extract 'org/repo' from a GitHub remote URL (HTTPS or SSH)."""
    patterns = [
        r"https://[^@]+@github\.com/(?P<org>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"https://github\.com/(?P<org>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
        r"git@github\.com:(?P<org>[^/]+)/(?P<repo>.+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        m = re.match(pattern, remote_url)
        if m:
            return f"{m.group('org')}/{m.group('repo')}"
    raise SystemExit(f"  error: cannot extract GitHub repo from URL: {remote_url}")


def get_repo_from_path(cwd: str) -> str:
    """Read the remote.origin.url from git config and extract org/repo."""
    require_tool("git", "Install git: https://git-scm.com/downloads")
    r = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    remote_url = r.stdout.strip()
    if r.returncode != 0 or not remote_url:
        raise click.ClickException(
            f"  error: no 'origin' remote configured for the repository at {cwd}.\n"
            "  Add one with: git remote add origin <github-url>"
        )
    return extract_repo(remote_url)


# ── Version management ────────────────────────────────────────────────


def update_version(pyproject_path: str, version: str):
    """Replace the version field in a pyproject.toml file."""
    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()
    new_content = re.sub(
        r'(?m)^version\s*=\s*["\'].*?["\']',
        f'version = "{version}"',
        content,
    )
    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    click.echo(f"  version  {version} written to pyproject.toml")


def commit_locally(cwd: str, version: str, subject: str = "bump version") -> None:
    """Stage all changes and commit. Nothing leaves the machine here.

    A local commit is reversible, so it happens before the first irreversible
    step and is pushed only once the un-undoable channel has accepted.
    """
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if not r.stdout.strip():
        click.echo("  commit   working tree clean, nothing to commit")
        return

    try:
        subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: {subject} to {version}"],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho(
            f"  error: git operation failed: {e.stderr.strip() if e.stderr else e}",
            fg="red",
        )
        click.secho(
            "  The version was bumped but not committed. Fix and retry.", fg="yellow"
        )
        raise SystemExit(1)
    click.echo("  commit   changes committed locally")


def _git_out(cwd: str, args: list[str]) -> str | None:
    r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else None


def push_main(cwd: str) -> None:
    """Push HEAD to origin/main and verify that origin/main really carries it.

    Pushing is not the same as having pushed. A tag whose commit is on no remote
    branch shows up on GitHub as a release of source nobody can find, and that
    is what happens when a clean working tree is read as "nothing to do".
    """
    try:
        subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        click.secho(f"  error: could not push to origin/main: {stderr}", fg="red")
        raise SystemExit(1)

    head = _git_out(cwd, ["rev-parse", "HEAD"])
    subprocess.run(["git", "fetch", "origin", "main"], cwd=cwd, capture_output=True)
    remote = _git_out(cwd, ["rev-parse", "origin/main"])
    if head and remote and head != remote:
        # Not necessarily wrong (someone else may have pushed on top), but the
        # tag must describe a commit the remote branch contains.
        contains = subprocess.run(
            ["git", "merge-base", "--is-ancestor", head, "origin/main"],
            cwd=cwd,
            capture_output=True,
        )
        if contains.returncode != 0:
            click.secho(
                "  error: origin/main does not contain the commit being released.",
                fg="red",
            )
            click.secho(
                "  Tagging it would publish a version whose source is on no branch. "
                "Reconcile the branch and retry.",
                fg="yellow",
            )
            raise SystemExit(1)
    click.echo("  push     origin/main updated")


def git_is_clean(cwd: str) -> bool:
    """Whether the working tree has no uncommitted change."""
    r = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=cwd
    )
    return r.returncode == 0 and not r.stdout.strip()


def local_tag_exists(cwd: str, tag: str) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}"],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return r.returncode == 0


def head_is_at_tag(cwd: str, tag: str) -> bool:
    """Whether HEAD is the exact commit *tag* points at.

    Resuming builds the package from the working tree, so this is what stops a
    resume from uploading artifacts that do not match what the tag says they are.
    """

    def _commit(ref: str) -> str | None:
        r = subprocess.run(
            ["git", "rev-list", "-n", "1", ref],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        return r.stdout.strip() if r.returncode == 0 else None

    head = _commit("HEAD")
    tagged = _commit(f"refs/tags/{tag}")
    return bool(head and tagged and head == tagged)


def read_project_version(pyproject_path: str) -> str | None:
    """The version currently written in ``[project].version``."""
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) and version.strip() else None


def sync_main_if_needed(cwd: str) -> None:
    """Make sure origin/main carries the commit that is about to be tagged.

    A resume can run on a repository where the release commit was made and
    never pushed, which is exactly the state a failed upload leaves behind.
    Pushing the tag alone there would publish a version whose source is on no
    branch. Only a fast-forward is ever pushed; anything else is reported and
    left alone, because a recovery command must not resolve a divergence in
    someone's branch on its own.
    """
    subprocess.run(["git", "fetch", "origin", "main"], cwd=cwd, capture_output=True)
    head = _git_out(cwd, ["rev-parse", "HEAD"])
    remote = _git_out(cwd, ["rev-parse", "origin/main"])
    if not head or not remote or head == remote:
        return

    def _is_ancestor(a: str, b: str) -> bool:
        return (
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", a, b],
                cwd=cwd,
                capture_output=True,
            ).returncode
            == 0
        )

    if _is_ancestor(head, "origin/main"):
        return
    if _is_ancestor(remote, "HEAD"):
        push_main(cwd)
        return
    click.secho(
        "  warn: origin/main and this checkout have diverged, so the commit "
        "being tagged was not pushed. Reconcile the branch yourself.",
        fg="yellow",
    )


def create_and_push_tag(cwd: str, version: str, *, push: bool = True):
    """Create an annotated tag at HEAD and, unless told otherwise, push it.

    An existing tag that points somewhere else is refused rather than reused:
    the artifacts are built from this working tree, so a tag describing another
    commit would mean the published source and the published package differ,
    and neither of the two can be corrected afterwards.
    """
    tag = version if version.startswith("v") else f"v{version}"
    try:
        subprocess.run(
            ["git", "fetch", "origin", "--tags"],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
        tags = subprocess.run(
            ["git", "tag"],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=True,
        ).stdout.splitlines()

        if tag not in tags:
            subprocess.run(
                ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
                cwd=cwd,
                check=True,
                capture_output=True,
            )
            click.echo(f"  tag      {tag} created")
        elif head_is_at_tag(cwd, tag):
            click.secho(
                f"  tag      {tag} already exists at this commit, kept", fg="yellow"
            )
        else:
            click.secho(
                f"  error: {tag} already exists and points at another commit.",
                fg="red",
            )
            click.secho(
                "  The package is built from this working tree, so publishing it "
                f"under {tag} would describe source that is not what {tag} contains. "
                "Release a new version instead.",
                fg="yellow",
            )
            raise SystemExit(1)

        if not push:
            click.secho(
                f"  tag      {tag} kept LOCAL, github is off for this release",
                fg="yellow",
            )
            return tag

        subprocess.run(
            ["git", "push", "origin", tag],
            cwd=cwd,
            check=True,
            capture_output=True,
        )
        click.echo(f"  tag      {tag} pushed to origin")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip() if e.stderr else str(e)
        click.secho(f"  error: git tag operation failed: {stderr}", fg="red")
        raise SystemExit(1)

    return tag


# ── GitHub release ────────────────────────────────────────────────────


def release_body(repo: str, tag: str, *, on_pypi: bool = True) -> str:
    """The Markdown body of a GitHub Release.

    When the release does not go to PyPI the body says so, so the asymmetry is
    visible to anyone reading the release instead of only to whoever typed the
    flag.
    """
    version_number = tag.lstrip("v")
    package = repo.split("/")[-1]
    body = (
        f"## {tag}\n\n"
        f"Automated release by **SPLENT**.\n\n"
        f"| | |\n|---|---|\n"
        f"| Tag | `{tag}` |\n"
        f"| Package | `{package}` |\n"
        f"| Version | `{version_number}` |\n\n"
    )
    if on_pypi:
        body += f"### Install\n```\npip install {package}=={version_number}\n```\n"
    else:
        body += (
            "### Install\n"
            "This version is **not published on PyPI**. It was released to GitHub "
            "only, on purpose. Install it from this tag.\n"
            f"```\npip install git+https://github.com/{repo}@{tag}\n```\n"
        )
    return body


def manual_github_release_command(repo: str, tag: str, resume_target: str) -> str:
    """The exact command that finishes the whole release, not just its GitHub half."""
    return f"splent release:resume {resume_target}"


def create_github_release(
    repo: str,
    version: str,
    token: str | None,
    *,
    on_pypi: bool = True,
    resume_target: str | None = None,
    fatal: bool = True,
) -> str | None:
    """Create a GitHub Release via the API.

    Returns ``None`` on success and the reason on failure. A failure is never
    swallowed: with ``fatal`` it exits non zero, and without it the caller is
    responsible for reporting it, which is what lets a release finish the
    channels that CAN still be finished before it reports the one that cannot.
    """
    tag = version if version.startswith("v") else f"v{version}"
    target = resume_target or repo.split("/")[-1]

    def _fail(reason: str, *, extra: str = "") -> str:
        click.secho(f"  github   release FAILED, {reason}", fg="red")
        if extra:
            click.secho(f"           {extra}", fg="bright_black")
        if fatal:
            click.echo()
            click.secho(
                f"  error: {repo} is now tagged {tag} with no GitHub release. "
                "The channels have diverged.",
                fg="red",
                bold=True,
            )
            click.secho(
                "  Finish the publication, on every channel that is still missing, "
                "without bumping the version with",
                fg="yellow",
            )
            click.echo(f"    {manual_github_release_command(repo, tag, target)}")
            click.echo()
            raise SystemExit(1)
        return reason

    if not token:
        return _fail("GITHUB_TOKEN is not set")

    if "/" not in repo:
        return _fail(f"'{repo}' is not an org/repo reference")
    org, name = repo.split("/", 1)

    from splent_cli.services import registry

    try:
        result = registry.github_create_release(
            org,
            name,
            tag=tag,
            name=f"Release {tag}",
            body=release_body(repo, tag, on_pypi=on_pypi),
            token=token,
        )
    except registry.RegistryError as e:
        return _fail(f"GitHub could not be reached ({e})")

    if result.created:
        click.echo(f"  github   release created {result.url or tag}")
        return None
    if result.already_exists:
        click.secho(f"  github   release for {tag} already exists, kept", fg="yellow")
        return None
    if result.rate_limited:
        return _fail(
            f"GitHub is rate limiting this token (HTTP {result.status})",
            extra="This is a rate limit, not a credential problem.",
        )
    if result.status == 401:
        return _fail(
            "GITHUB_TOKEN was rejected (HTTP 401)",
            extra="The token is invalid, expired or revoked.",
        )
    if result.status == 403:
        return _fail(
            "GITHUB_TOKEN is not allowed to create releases here (HTTP 403)",
            extra="The token needs the 'repo' scope for this repository.",
        )
    return _fail(f"GitHub answered HTTP {result.status}", extra=result.detail)


# ── PyPI ──────────────────────────────────────────────────────────────


def _looks_rate_limited(output: str) -> bool:
    """Whether twine's output is PyPI saying "too many", not "who are you"."""
    lowered = (output or "").lower()
    return "429" in lowered or "too many" in lowered


def _looks_forbidden(output: str) -> bool:
    """Whether twine's output is PyPI refusing the credentials or the scope."""
    lowered = (output or "").lower()
    return (
        "403" in lowered
        or "401" in lowered
        or "forbidden" in lowered
        or "not allowed to upload" in lowered
        or "invalid or non-existent authentication" in lowered
    )


def read_project_name(pyproject_path: str) -> str | None:
    """The distribution name from ``[project].name``, or ``None``.

    This is the name PyPI knows the package by, which is not always the
    directory name, and it is what the gate must ask PyPI about.
    """
    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    name = data.get("project", {}).get("name")
    return name if isinstance(name, str) and name.strip() else None


def twine_env() -> dict:
    """The environment twine will run with, carrying the credentials the gate checked.

    twine reads TWINE_USERNAME and TWINE_PASSWORD and nothing else, so the
    PYPI_USERNAME / PYPI_PASSWORD spelling the rest of the CLI accepts is
    written under the names twine reads. Without this the gate can verify one
    credential and the upload run with none.
    """
    env = os.environ.copy()
    username, password = release_gate.pypi_credentials()
    if username:
        env["TWINE_USERNAME"] = username
    if password:
        env["TWINE_PASSWORD"] = password
    return env


def build_and_upload_pypi(
    path: str, *, resume_target: str | None = None, diverged: bool = True
):
    """Build the package and upload to PyPI.

    ``diverged`` says whether a failure here leaves the channels out of step. In
    the release pipeline the upload runs BEFORE anything is pushed, so a refusal
    costs a retry and nothing else; on a resume the tag is already out there and
    the failure is a real divergence. The message says which of the two it is.
    """
    click.echo("  pypi     building package...")
    subprocess.run(["rm", "-rf", "dist"], cwd=path)
    try:
        subprocess.run(
            [sys.executable, "-m", "build"],
            check=True,
            cwd=path,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        click.secho(
            f"  error: package build failed: {e.stderr.strip() if e.stderr else ''}",
            fg="red",
        )
        raise SystemExit(1)

    click.echo("  pypi     uploading...")

    # Expand dist/* ourselves: subprocess runs without a shell, so a literal
    # "dist/*" would be passed verbatim to twine and never match any file.
    import glob

    dist_files = sorted(glob.glob(os.path.join(path, "dist", "*")))
    if not dist_files:
        click.secho("  error: no built artifacts found in dist/", fg="red")
        raise SystemExit(1)

    # twine reads TWINE_USERNAME / TWINE_PASSWORD from the environment and
    # nothing else, so twine_env() puts the resolved credentials under exactly
    # those names. --non-interactive makes twine fail loudly instead of blocking
    # on a credentials prompt; --skip-existing keeps re-runs idempotent (a
    # half-uploaded release can be retried safely).
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "twine",
                "upload",
                "--non-interactive",
                "--skip-existing",
                *dist_files,
            ],
            env=twine_env(),
            check=True,
            cwd=path,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        if stderr:
            click.secho(stderr, fg="bright_black")

        if _looks_rate_limited(stderr):
            click.secho(
                f"  error: PyPI REFUSED the upload, rate limited (exit {e.returncode}).",
                fg="red",
            )
            click.secho(
                "  This is a rate limit, not a credential problem. "
                + release_gate.pypi_window(None),
                fg="yellow",
            )
        elif _looks_forbidden(stderr):
            click.secho(
                f"  error: PyPI REFUSED the upload, not authorized (exit "
                f"{e.returncode}).",
                fg="red",
            )
            click.secho(
                "  This is a credential or token scope problem, not a rate limit. "
                "Waiting will not fix it.",
                fg="yellow",
            )
        else:
            click.secho(
                f"  error: PyPI upload failed (exit {e.returncode}).",
                fg="red",
            )

        click.echo()
        target = resume_target or "<name>"
        if diverged:
            click.secho(
                "  The tag is already pushed, so GitHub and PyPI have DIVERGED for "
                "this version.",
                fg="red",
                bold=True,
            )
            click.secho(
                "  Finish the publication without bumping the version with", fg="yellow"
            )
            click.echo(f"    splent release:resume {target}")
        else:
            click.secho(
                "  Nothing was pushed. No tag, no GitHub release, so the channels "
                "did NOT diverge.",
                fg="yellow",
                bold=True,
            )
            click.secho(
                "  The version bump is committed locally. Retry the same version, "
                "without bumping it again, with",
                fg="yellow",
            )
            click.echo(f"    splent release:resume {target}")
        click.secho(
            "  The artifacts are already built in dist/, so a manual "
            "'twine upload dist/*' also works.",
            fg="bright_black",
        )
        raise SystemExit(1)

    click.echo("  pypi     upload complete")


# ── Semver wizard ─────────────────────────────────────────────────────


def fetch_latest_tag(org: str, repo_name: str, *, strict: bool = False) -> str | None:
    """Return the highest semver tag from GitHub, or None.

    Fetches up to 100 tags and sorts by semver (not creation date). With
    ``strict`` a failure raises instead of answering ``None``, so "this repo has
    no tags" is never manufactured out of "GitHub did not answer".
    """
    from splent_cli.services import registry

    return registry.latest_semver_tag(org, repo_name, strict=strict)


def local_latest_tag(cwd: str) -> str | None:
    """The highest semver tag in the local clone, or ``None``.

    Local truth the wizard can fall back on when GitHub will not answer. A clone
    that was ever fetched knows its own tags without the network.
    """
    from splent_cli.services import registry

    r = subprocess.run(["git", "tag"], capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        return None
    ordered = registry.semver_sorted(r.stdout.split())
    return ordered[0] if ordered else None


def highest_known_version(
    remote_tag: str | None, local_tag: str | None, project_version: str | None
) -> str | None:
    """The highest of the three things that know what version this package is at.

    The wizard offers the NEXT version, so it has to start from the highest
    version any source knows about. Starting from a lower one produces a bump
    that goes backwards, and a tag cannot be taken back.
    """
    best = None
    best_key = None
    for candidate in (remote_tag, local_tag, project_version):
        if not candidate:
            continue
        key = release_gate.semver_key(candidate)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best, best_key = candidate, key
    return best


def bump(version_str: str, bump_type: str) -> str:
    """Return the next version string for the given bump type."""
    clean = version_str.lstrip("v")
    parts = clean.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise click.ClickException(
            f"Cannot parse version '{version_str}', expected vMAJOR.MINOR.PATCH"
        )
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if bump_type == "major":
        return f"v{major + 1}.0.0"
    if bump_type == "minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def guard_incomplete_release(
    org: str,
    repo_name: str,
    package: str,
    *,
    channels=None,
    resume_target: str | None = None,
) -> None:
    """Refuse to start a NEW release while the last one never finished.

    Bumping over an unfinished release is how versions get burned: the tag is
    already out there, the package never reached PyPI, and a rerun asks for the
    next number instead of finishing the previous one.

    This is the early, friendly copy of the check. The binding one lives in the
    gate (:func:`release_gate.run_gate`), which runs on every path, including
    the one that passes the version on the command line and never opens the
    wizard. A lookup failure here is reported and left to the gate, which
    refuses rather than assuming everything is fine.
    """
    from splent_cli.services import registry

    try:
        incomplete = release_gate.find_incomplete_release(
            f"{org}/{repo_name}", package, channels=channels, strict=True
        )
    except registry.RegistryError as e:
        click.secho(
            f"  warn: could not check whether the last release finished ({e}).",
            fg="yellow",
        )
        return
    if not incomplete:
        return

    click.echo()
    click.secho(
        f"  error: {incomplete.description}.",
        fg="red",
        bold=True,
    )
    click.secho(
        "  That release never finished, so the channels are already diverged. "
        "Releasing a new version now would burn a version number and leave the "
        "old one broken.",
        fg="yellow",
    )
    click.secho("  Finish it first with", fg="yellow")
    click.echo(f"    splent release:resume {resume_target or repo_name}")
    click.echo()
    raise SystemExit(1)


def semver_wizard(
    org: str,
    repo_name: str,
    package: str | None = None,
    *,
    channels=None,
    resume_target: str | None = None,
    path: str | None = None,
) -> str:
    """Interactive wizard: read the current version, offer bumps, return the choice.

    The current version is the highest of what GitHub reports, what the local
    clone is tagged with, and what pyproject.toml says. GitHub failing to answer
    is reported as a failure, never as "no tags found", because the two lead to
    very different offers and one of them regresses the version permanently.
    """
    click.echo()
    click.secho("  Reading the current version...", fg="bright_black")

    from splent_cli.services import registry

    remote = None
    remote_failed = None
    try:
        remote = fetch_latest_tag(org, repo_name, strict=True)
    except registry.RegistryError as e:
        remote_failed = str(e)

    local = local_latest_tag(path) if path else None
    declared = (
        read_project_version(os.path.join(path, "pyproject.toml")) if path else None
    )
    current = highest_known_version(remote, local, declared)

    if remote_failed:
        click.secho(
            f"  warn: GitHub would not list the tags of {org}/{repo_name} "
            f"({remote_failed}).",
            fg="yellow",
        )
        if current:
            click.secho(
                f"  Using the highest version this clone knows about, {current}. "
                "It may be behind the remote.",
                fg="yellow",
            )
        else:
            click.secho(
                "  Nothing local knows about a version either, so the current "
                "version is unknown.",
                fg="red",
            )
            click.secho(
                "  Refusing to guess. A version chosen from a failed lookup can "
                "land below what is already tagged, and a tag cannot be taken back.",
                fg="yellow",
            )
            raise SystemExit(1)

    if package:
        guard_incomplete_release(
            org,
            repo_name,
            package,
            channels=channels,
            resume_target=resume_target,
        )

    if current:
        click.echo(f"  Current version: {click.style(current, fg='cyan', bold=True)}")
    else:
        click.secho(
            "  No tags on GitHub, none locally, and no version in pyproject.toml. "
            "This is the first release.",
            fg="yellow",
        )
        current = "v0.0.0"

    patch_v = bump(current, "patch")
    minor_v = bump(current, "minor")
    major_v = bump(current, "major")

    click.echo()
    click.echo(click.style("  Bump type:", bold=True))
    click.echo(
        f"    {click.style('[1]', bold=True)} patch  {click.style(patch_v, fg='green')}"
        f"   bug fixes, no new features"
    )
    click.echo(
        f"    {click.style('[2]', bold=True)} minor  {click.style(minor_v, fg='yellow')}"
        f"   new features, backward compatible"
    )
    click.echo(
        f"    {click.style('[3]', bold=True)} major  {click.style(major_v, fg='red')}"
        f"   breaking changes"
    )
    click.echo(f"    {click.style('[4]', bold=True)} cancel")
    click.echo()

    choice = click.prompt(
        "  Choice",
        type=click.Choice(["1", "2", "3", "4"]),
        show_choices=False,
    )
    if choice == "4":
        raise SystemExit("  Release cancelled.")

    chosen = {"1": patch_v, "2": minor_v, "3": major_v}[choice]

    click.echo()
    click.echo(f"  Will release as {click.style(chosen, fg='cyan', bold=True)}")
    click.confirm("  Proceed?", abort=True)
    return chosen


# ── Pre-release gate (lint + tests) ───────────────────────────────────

_RUFF_HINT = "Install it with: pip install ruff"


def _lint_path(path: str) -> bool:
    """Run ruff check + ruff format --check on `path`. True if clean.

    Mirrors `splent lint` (the project's canonical lint = ruff, NOT black).
    """
    require_tool("ruff", _RUFF_HINT)
    ok = True
    check = run(
        ["ruff", "check", path], check=False, capture=True, tool_hint=_RUFF_HINT
    )
    if check.returncode != 0:
        ok = False
        out = (check.stdout + check.stderr).strip()
        if out:
            click.secho(out, fg="red")
    fmt = run(
        ["ruff", "format", "--check", path],
        check=False,
        capture=True,
        tool_hint=_RUFF_HINT,
    )
    if fmt.returncode != 0:
        ok = False
        out = (fmt.stdout + fmt.stderr).strip()
        if out:
            click.secho(out, fg="yellow")
    return ok


def _test_entity(kind: str, name: str, path: str) -> bool:
    """Run the released entity's test suite. True if it passes.

    Uses the project's own test runners so "passing tests" means the same here
    as everywhere else:
      - cli / framework: pytest in the package directory
      - feature:         splent feature:test <name>
      - product:         splent product:test
    """
    if kind in ("cli", "framework"):
        result = run(
            [sys.executable, "-m", "pytest", "tests", "-q"],
            cwd=path,
            check=False,
        )
    elif kind == "feature":
        feature_name = name.split("/")[-1]
        result = run(
            [sys.executable, "-m", "splent_cli", "feature:test", feature_name],
            check=False,
        )
    elif kind == "product":
        result = run(
            [sys.executable, "-m", "splent_cli", "product:test"],
            check=False,
        )
    else:
        return True
    return result.returncode == 0


def run_pre_release_checks(kind: str, name: str, path: str) -> None:
    """Gate a release on lint + tests for the entity.

    Aborts with SystemExit(1) on any failure. Always called BEFORE the first
    irreversible step (version bump / commit / tag push / PyPI upload), so a
    failure leaves the repo and remotes untouched.
    """
    click.echo()
    click.secho("  Pre-release checks (lint + tests)", bold=True)

    click.echo("  checks   ruff (lint + format)...")
    if _lint_path(path):
        click.secho("  checks   lint OK", fg="green")
    else:
        click.secho(
            "  error: lint failed. Fix it (try `splent lint --fix`) before "
            "releasing, or re-run with --skip-checks.",
            fg="red",
        )
        raise SystemExit(1)

    click.echo("  checks   tests...")
    if _test_entity(kind, name, path):
        click.secho("  checks   tests OK", fg="green")
    else:
        click.secho(
            f"  error: tests failed for {name}. Fix them before releasing, "
            "or re-run with --skip-checks.",
            fg="red",
        )
        raise SystemExit(1)


# ── Orchestrator ──────────────────────────────────────────────────────


def resolve_channels(
    path: str,
    *,
    no_github: bool = False,
    no_pypi: bool = False,
    extra=(),
):
    """The channels this release publishes to.

    Starts from what the package itself declares in ``[tool.splent.release]``
    and subtracts whatever the operator turned off for this run. Turning a
    channel off is always an explicit act, never a default and never the
    consequence of a failure, and a declaration that cannot be read is an
    error rather than a silent fallback to publishing everywhere.
    """
    try:
        channels = list(
            release_gate.declared_channels(os.path.join(path, "pyproject.toml"))
        )
    except release_gate.ChannelDeclarationError as e:
        click.secho(f"  error: {e}", fg="red")
        click.secho(
            "  Fix the declaration. Guessing here could publish to a channel this "
            "package was meant to stay off, and PyPI cannot be undone.",
            fg="yellow",
        )
        raise SystemExit(1)
    for channel in extra:
        if channel not in channels:
            channels.append(channel)
    if no_github and release_gate.GITHUB in channels:
        channels.remove(release_gate.GITHUB)
    if no_pypi and release_gate.PYPI in channels:
        channels.remove(release_gate.PYPI)
    return release_gate.normalize_channels(channels)


def confirm_single_channel(channels, *, no_github: bool = False, no_pypi: bool = False):
    """Make an ad-hoc one-channel release an explicit, deliberate act.

    A package that publishes to one channel by policy declares it once in its
    own pyproject and is not asked again. Turning a channel off for a single
    run with a flag is different: it creates an asymmetry nobody else can see
    from the repository, so it is confirmed out loud, and refused by default
    when there is nobody to ask.
    """
    if not (no_github or no_pypi):
        return

    off = "PyPI" if no_pypi else "GitHub"
    on = "GitHub" if no_pypi else "PyPI"
    click.echo()
    click.secho(
        f"  Releasing to {on} ONLY. {off} is turned off.", fg="yellow", bold=True
    )
    click.secho(
        f"  This version will exist on {on} and not on {off}. That is a divergence "
        "you are creating on purpose, and nothing will fix it later.",
        fg="yellow",
    )
    if no_github:
        click.secho(
            "  Nothing at all goes to GitHub, so the commit and the tag stay on "
            "this machine. Push them yourself if you want them there.",
            fg="yellow",
        )
    if not channels:
        click.secho("  error: that leaves no channel at all.", fg="red")
        raise SystemExit(1)
    click.confirm("  Continue?", default=False, abort=True)


def run_release_pipeline(
    name: str,
    path: str,
    version: str,
    *,
    kind: str = "package",
    require_docker: bool = False,
    skip_checks: bool = False,
    channels=None,
    resume_target: str | None = None,
    allow_unfinished: bool = False,
    docker_image: str | None = None,
    pre_commit_hook=None,
    post_pypi_hook=None,
):
    """Run the standard release pipeline.

    \b
     1. Validate the environment (presence of credentials)
     2. Release gate, every enabled channel, before anything is mutated
     3. Lint and tests
     4. Release gate again, so the verdict acted on is seconds old, not minutes
     -- everything below changes something --
     5. Update the version in pyproject.toml           (local, revertible)
     6. (optional) pre_commit_hook, e.g. the contract  (local, revertible)
     7. Commit                                         (local, revertible)
     8. Build and upload to PyPI
     9. Push the branch, the tag, and create the GitHub release
    10. (optional) post_pypi_hook, e.g. the snapshot or the Docker image

    The order of 8 and 9 is the point. A PyPI version can never be replaced or
    removed; a tag that was never pushed costs nothing. Uploading first means a
    PyPI refusal, including the rate limit on creating a new project, leaves
    nothing published anywhere and no divergence to repair. The reverse order is
    what put 13 features on GitHub with nothing on PyPI.

    Hooks receive (path, version) as arguments.
    """
    normalized = version.lstrip("v")
    tag = f"v{normalized}"
    pyproject = os.path.join(path, "pyproject.toml")

    if not os.path.isdir(path):
        raise SystemExit(f"  error: directory not found: {path}")
    if not os.path.isfile(pyproject):
        raise SystemExit(f"  error: pyproject.toml not found in {path}")

    if channels is None:
        channels = resolve_channels(path)
    channels = release_gate.normalize_channels(channels)
    if not channels:
        click.secho(
            "  error: every channel is turned off, so this release would publish "
            "nowhere.",
            fg="red",
        )
        raise SystemExit(1)

    to_pypi = release_gate.PYPI in channels
    to_github = release_gate.GITHUB in channels
    to_docker = release_gate.DOCKER in channels

    validate_release_env(
        require_pypi=to_pypi,
        require_docker=require_docker and to_docker,
        require_github=to_github,
    )

    # Validate git availability + origin remote up front, before any
    # state-changing step. This fails fast with an actionable message instead of
    # crashing mid-pipeline.
    repo = get_repo_from_path(path)
    package = read_project_name(pyproject) or repo.split("/")[-1]
    target = resume_target or package

    click.echo()
    click.echo(click.style(f"  Releasing {name} {tag}", bold=True))

    def _gate(deep: bool, header: bool = True):
        report = release_gate.run_gate(
            repo=repo,
            package=package,
            version=normalized,
            channels=channels,
            docker_image=docker_image,
            deep=deep,
            allow_unfinished=allow_unfinished,
        )
        release_gate.render(report, header=header)
        if not report.ok:
            release_gate.refuse(report, resume_target=target)
        return report

    # Gate, pass 1: cheap and early, so a bad token or an already published
    # version costs seconds instead of a full test run. Its verdict is not the
    # one acted on; pass 2 below is.
    _gate(deep=False)

    # Lint + tests, still before the first mutation. Skippable for emergencies.
    if skip_checks:
        click.echo()
        click.secho(
            "  WARNING  skipping pre-release lint and tests (--skip-checks).",
            fg="yellow",
        )
    else:
        run_pre_release_checks(kind, name, path)

    # Gate, pass 2: the binding one. It runs immediately before the first
    # mutation, so the answer acted on cannot have gone stale during the test
    # run, and it names the project to PyPI, which is what exercises the
    # project-creation rate limit and the per-project token scope.
    click.echo()
    click.secho("  Re-checking the channels, right before publishing", bold=True)
    report = _gate(deep=True, header=False)

    if report.unproven:
        # Whatever could not be proven, the ordering below means a refusal
        # cannot leave the channels diverged. Say so instead of asking.
        click.echo()
        click.secho(
            "  Part of this could not be proven in advance. PyPI is published "
            "first, so if it refuses, nothing will have been pushed and nothing "
            "will have diverged.",
            fg="yellow",
        )

    # ── Everything below this line changes the world ──────────────────
    click.echo()

    update_version(pyproject, normalized)

    if pre_commit_hook:
        pre_commit_hook(path, normalized)

    commit_locally(path, tag, subject=f"release {name}")

    # PyPI first: it is the only step that can never be undone or retried for
    # the same version.
    if to_pypi:
        from splent_cli.commands.clear.clear_build import clean_build_artifacts

        clean_build_artifacts(path, quiet=True)
        build_and_upload_pypi(path, resume_target=target, diverged=False)
    else:
        click.secho(
            "  pypi     upload SKIPPED on purpose (--no-pypi), "
            f"{package} {normalized} will not exist on PyPI",
            fg="yellow",
        )

    failures: list[str] = []

    if to_github:
        push_main(path)
        create_and_push_tag(path, tag)
        failure = create_github_release(
            repo,
            tag,
            os.getenv("GITHUB_TOKEN"),
            on_pypi=to_pypi,
            resume_target=target,
            fatal=False,
        )
        if failure:
            failures.append(f"the GitHub release for {tag} ({failure})")
    else:
        create_and_push_tag(path, tag, push=False)
        click.secho(
            "  github   nothing published, --no-github. The commit and the tag "
            "are local only.",
            fg="yellow",
        )

    if post_pypi_hook:
        post_pypi_hook(path, normalized)

    click.echo()
    if failures:
        done = [c for c in channels if c != release_gate.GITHUB]
        where = "published on " + ", ".join(done) if done else "tagged"
        click.secho(
            f"  error: {name} {tag} is {where} but "
            + " and ".join(failures)
            + " could not be created.",
            fg="red",
            bold=True,
        )
        click.secho(
            "  The channels have DIVERGED for this version. Finish it, without "
            "bumping the version, with",
            fg="yellow",
        )
        click.echo(f"    splent release:resume {target}")
        click.echo()
        raise SystemExit(1)

    # The index the marketplace serves lives in another repository, so a
    # release here does not reach it on its own. Ask it to rebuild, as the
    # last step and best effort: the release is already done.
    from splent_cli.services import index_refresh

    index_refresh.request_rebuild()

    click.secho(f"  {name} {tag} released.", fg="green")
    if not to_pypi or not to_github:
        published = ", ".join(channels)
        click.secho(
            f"  Published to {published} only. The channels do not match for this "
            "version, on purpose.",
            fg="yellow",
        )
    click.echo()
