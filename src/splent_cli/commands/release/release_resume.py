"""
release:resume — Finish a release that reached one channel and not the other.

A gate lowers the odds of a mid-flight failure, it does not remove them. When a
channel refuses after another one has accepted, the version is stuck: re-running
feature:release would BUMP THE VERSION AGAIN and burn a number while leaving the
broken one behind. That is exactly how several features reached v0.2.1 without
ever reaching PyPI.

This command is the other path. It never bumps. It reads the version that is
already committed, asks each channel what is actually missing, and publishes
only that.

Two rules make it usable in the state it exists for, which is a workspace where
something is already wrong:

  * a channel that is blocked never stops a channel that is not. If GitHub is
    401 and PyPI is the only thing missing, the PyPI half runs
  * it never moves the operator's checkout. When the tag is not at HEAD the
    package is built from a temporary worktree of the tag
"""

import contextlib
import os
import shutil
import subprocess
import tempfile

import click

from splent_cli.services import context, release, release_gate


# ── Target resolution ─────────────────────────────────────────────────


def resolve_target(target: str, workspace: str) -> tuple[str, str, str]:
    """Map a target name to (path, label, kind).

    Accepts ``cli``, ``framework``, ``product``, or a feature reference such as
    ``splent_feature_auth`` / ``splent-io/splent_feature_auth``. For a feature
    the label is ``namespace/name``, which is what the snapshot step needs.
    """
    alias = target.strip().lower()

    if alias in ("cli", "splent_cli"):
        return os.path.join(workspace, "splent_cli"), "splent_cli", "cli"
    if alias in ("framework", "splent_framework"):
        return (
            os.path.join(workspace, "splent_framework"),
            "splent_framework",
            "framework",
        )
    if alias == "product":
        product = context.require_app()
        return os.path.join(workspace, product), product, "product"

    from splent_cli.commands.feature.feature_release import resolve_feature_path

    path, namespace, feature_name = resolve_feature_path(target, workspace)
    return path, f"{namespace}/{feature_name}", "feature"


def _feature_snapshot_path(workspace: str, label: str, tag: str) -> str | None:
    """Where the read-only snapshot of a released feature version lives."""
    if "/" not in label:
        return None
    from splent_cli.utils.feature_utils import normalize_namespace

    namespace, feature_name = label.split("/", 1)
    return os.path.join(
        workspace,
        ".splent_cache",
        "features",
        normalize_namespace(namespace),
        f"{feature_name}@{tag}",
    )


@contextlib.contextmanager
def build_tree(path: str, tag: str):
    """Yield a directory holding exactly the source *tag* points at.

    When there is no such tag yet, or HEAD is already the tagged commit, that
    is the working tree itself. Otherwise a detached worktree of the tag is
    created in a temporary directory and removed afterwards, so the operator's
    checkout is never moved. In a SPLENT workspace a feature directory is
    symlinked into the running product, and checking out a tag there would
    silently change what the product serves.
    """
    if not release.local_tag_exists(path, tag) or release.head_is_at_tag(path, tag):
        yield path
        return

    tmp = tempfile.mkdtemp(prefix="splent-resume-")
    worktree = os.path.join(tmp, "src")
    r = subprocess.run(
        ["git", "worktree", "add", "--detach", worktree, tag],
        cwd=path,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        click.secho(
            f"  error: could not check out {tag} to build it: {r.stderr.strip()}",
            fg="red",
        )
        raise SystemExit(1)
    click.secho(
        f"  build    from a temporary worktree of {tag}, this checkout is untouched",
        fg="bright_black",
    )
    try:
        yield worktree
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", worktree],
            cwd=path,
            capture_output=True,
        )
        shutil.rmtree(tmp, ignore_errors=True)


# ── Command ───────────────────────────────────────────────────────────


@click.command(
    "release:resume",
    short_help="Finish an interrupted release without bumping the version.",
)
@click.argument("target")
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Do not ask for confirmation before publishing what is missing.",
)
@click.option(
    "--no-pypi",
    is_flag=True,
    help="Do not touch PyPI, even if something is missing there.",
)
@click.option(
    "--no-github",
    is_flag=True,
    help="Do not touch GitHub, even if something is missing there.",
)
def release_resume(target, assume_yes, no_pypi, no_github):
    """Publish the parts of the current version that never made it out.

    \b
    Examples:
        splent release:resume splent_feature_team
        splent release:resume splent_feature_team --no-github
        splent release:resume cli
        splent release:resume product
    """
    workspace = str(context.workspace())
    path, label, kind = resolve_target(target, workspace)

    pyproject = os.path.join(path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        click.secho(f"  error: pyproject.toml not found in {path}", fg="red")
        raise SystemExit(1)

    version = release.read_project_version(pyproject)
    if not version:
        click.secho(f"  error: no [project].version in {pyproject}", fg="red")
        raise SystemExit(1)

    normalized = version.lstrip("v")
    tag = f"v{normalized}"
    repo = release.get_repo_from_path(path)
    package = release.read_project_name(pyproject) or repo.split("/")[-1]
    channels = release.resolve_channels(
        path, no_github=no_github, no_pypi=no_pypi, extra=_extra_channels(kind)
    )
    if not channels:
        click.secho(
            "  error: every channel is turned off, nothing to resume.", fg="red"
        )
        raise SystemExit(1)

    docker_image = _docker_image(label) if release_gate.DOCKER in channels else None

    click.echo()
    click.secho(f"  Resuming {label} {tag}", bold=True)
    click.secho(
        "  The version is read from pyproject.toml and is never bumped here.",
        fg="bright_black",
    )

    local_tag = release.local_tag_exists(path, tag)
    if not local_tag and not release.git_is_clean(path):
        click.echo()
        click.secho("  error: the working tree has uncommitted changes.", fg="red")
        click.secho(
            f"  There is no {tag} yet, so the tag would be created at HEAD and the "
            "package built from this tree. Commit or stash first.",
            fg="yellow",
        )
        raise SystemExit(1)

    # ── What does each channel say is missing ─────────────────────────
    report = release_gate.run_gate(
        repo=repo,
        package=package,
        version=normalized,
        channels=channels,
        resume=True,
        docker_image=docker_image,
    )
    release_gate.render(report)

    gh = report.github
    pypi = report.pypi
    docker = report.docker

    usable = [st for st in report.statuses if st.ok]
    if not usable:
        click.echo()
        click.secho(
            "  error: no channel can be reached, so nothing can be finished.",
            fg="red",
            bold=True,
        )
        release_gate.refuse(report, resume_target=target)

    # A blocked channel is reported and skipped, never allowed to stop a channel
    # that is fine. The command exists to repair a workspace where something is
    # already broken; refusing to do the reachable half is how 13 features
    # became unrecoverable behind one unrelated token.
    stuck = [st for st in report.statuses if st.blocked]

    push_tag = bool(gh and gh.ok and not gh.tag_exists)
    make_release = bool(gh and gh.ok and not gh.already_published)
    upload = bool(pypi and pypi.ok and not pypi.already_published)
    push_image = bool(docker and docker.ok and not docker.already_published)
    # A feature release also produces a read-only snapshot in the cache. When
    # the original run died before that, the version is unusable locally even
    # once it is published, so finishing the job includes creating it.
    # The snapshot is a clone of the tag from GitHub, so it can only be created
    # once the tag is actually there.
    tag_on_remote = bool(gh and gh.ok and (gh.tag_exists or push_tag))
    snapshot_path = (
        _feature_snapshot_path(workspace, label, tag)
        if kind == "feature" and tag_on_remote
        else None
    )
    make_snapshot = bool(snapshot_path and not os.path.isdir(snapshot_path))

    steps = []
    if upload:
        steps.append(f"build and upload {package} {normalized} to PyPI")
    if push_tag:
        steps.append(f"push the tag {tag} to origin")
    if make_release:
        steps.append(f"create the GitHub release for {tag}")
    if push_image:
        steps.append(f"build and push the Docker image {docker_image}:{normalized}")
    if make_snapshot:
        steps.append(f"create the local snapshot for {tag}")

    click.echo()
    if not steps:
        if stuck:
            _report_stuck(stuck, label, tag)
            raise SystemExit(1)
        click.secho(
            f"  {label} {tag} is already complete on every channel it publishes to. "
            "Nothing to do.",
            fg="green",
        )
        click.echo()
        return

    click.secho("  Missing", bold=True)
    for step in steps:
        click.echo(f"    - {step}")
    if stuck:
        click.echo()
        click.secho(
            "  Cannot be finished in this run, the channel would not answer",
            fg="yellow",
            bold=True,
        )
        for st in stuck:
            click.secho(f"    {st.label:<9}{st.summary}", fg="yellow")
    click.echo()

    if not assume_yes:
        click.confirm("  Publish what is missing?", default=True, abort=True)

    failures: list[str] = []

    if upload:
        from splent_cli.commands.clear.clear_build import clean_build_artifacts

        with build_tree(path, tag) as source:
            clean_build_artifacts(source, quiet=True)
            release.build_and_upload_pypi(source, resume_target=target)

    if push_tag:
        # A tag must describe a commit the remote branch has. After a failed
        # upload the release commit can still be sitting here unpushed.
        release.sync_main_if_needed(path)
        if not release.local_tag_exists(path, tag):
            release.create_and_push_tag(path, tag)
        else:
            try:
                subprocess.run(
                    ["git", "push", "origin", tag],
                    cwd=path,
                    check=True,
                    capture_output=True,
                )
            except subprocess.CalledProcessError as e:
                stderr = (
                    e.stderr.decode()
                    if isinstance(e.stderr, bytes)
                    else (e.stderr or "")
                )
                click.secho(
                    f"  error: could not push {tag}, {stderr.strip()}", fg="red"
                )
                raise SystemExit(1)
            click.echo(f"  tag      {tag} pushed to origin")

    if make_release:
        failure = release.create_github_release(
            repo,
            tag,
            os.getenv("GITHUB_TOKEN"),
            on_pypi=release_gate.PYPI in channels,
            resume_target=target,
            fatal=False,
        )
        if failure:
            failures.append(f"the GitHub release for {tag} ({failure})")

    if push_image:
        from splent_cli.commands.product.product_release import release_docker_image

        product = label.split("/")[-1]
        release_docker_image(product, normalized, os.path.join(path, "docker"))

    if make_snapshot:
        from splent_cli.commands.feature.feature_release import (
            create_versioned_snapshot,
        )

        namespace, feature_name = label.split("/", 1)
        create_versioned_snapshot(namespace, feature_name, tag, workspace)

    click.echo()
    if failures or stuck:
        if failures:
            click.secho(
                "  error: " + " and ".join(failures) + " could not be created.",
                fg="red",
                bold=True,
            )
        if stuck:
            _report_stuck(stuck, label, tag)
        click.secho("  Run this command again once that is fixed.", fg="yellow")
        click.echo()
        raise SystemExit(1)

    click.secho(
        f"  {label} {tag} is now published on {', '.join(channels)}.", fg="green"
    )
    click.secho("  Confirm the whole workspace with", fg="bright_black")
    click.echo("    splent check:releases")
    click.echo()


def _report_stuck(stuck, label: str, tag: str) -> None:
    names = ", ".join(st.label for st in stuck)
    click.secho(f"  {label} {tag} is still incomplete on {names}.", fg="red", bold=True)
    for st in stuck:
        click.secho(f"    {st.label:<9}{st.summary}", fg="red")
        for hint in st.hints:
            click.secho(f"             {hint}", fg="bright_black")


def _extra_channels(kind: str):
    """A product also publishes a Docker image, so resuming has to cover it."""
    return (release_gate.DOCKER,) if kind == "product" else ()


def _docker_image(label: str) -> str | None:
    username = os.getenv("DOCKERHUB_USERNAME")
    if not username:
        return None
    return f"{username}/{label.split('/')[-1]}"


cli_command = release_resume
