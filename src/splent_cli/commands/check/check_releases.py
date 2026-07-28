"""
check:releases — Report GitHub/PyPI divergence across the workspace.

Answers, without anyone having to read a published index by hand, the two
questions a two-channel release can go wrong in:

    which packages carry a git tag that never reached PyPI
    which packages have a PyPI release that was never tagged

and, since a tag with no GitHub Release is the same class of failure, it
reports that third case too, for every tag rather than only the newest.

The one rule this command lives by: it never turns a failed lookup into a
finding. "GitHub would not answer" and "there are no tags" are different facts,
"PyPI would not answer" and "nothing is published" are different facts, and a
report that mixes them sends someone to republish packages that are fine.
Anything that could not be checked goes to its own bucket and makes the run
exit non zero, because not knowing is not the same as being in sync.

Every version comparison is done through services/registry.py, the single
GitHub and PyPI boundary. This command reads, it never publishes.
"""

import os
import subprocess

import click

from splent_cli.services import context, registry, release_gate
from splent_cli.services.release import extract_repo, read_project_name


CORE_PACKAGES = ("splent_cli", "splent_framework")


# ── Pure comparison, so the interesting part is testable ──────────────


def _strip_v(version: str) -> str:
    return version.lstrip("v")


def compare_channels(tags: list[str], pypi_versions: list[str]) -> dict:
    """Split two version lists into the three interesting buckets.

    Returns ``{"tag_only": [...], "pypi_only": [...], "both": [...]}`` with the
    tag spelling preserved for tags and the PyPI spelling for PyPI, compared on
    the version number alone so ``v1.2.3`` and ``1.2.3`` are the same release.
    """
    tag_map = {_strip_v(t): t for t in tags}
    pypi_map = {_strip_v(p): p for p in pypi_versions}

    def _key(v: str):
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return (0, 0, 0)

    tag_only = sorted((set(tag_map) - set(pypi_map)), key=_key, reverse=True)
    pypi_only = sorted((set(pypi_map) - set(tag_map)), key=_key, reverse=True)
    both = sorted((set(tag_map) & set(pypi_map)), key=_key, reverse=True)
    return {
        "tag_only": [tag_map[v] for v in tag_only],
        "pypi_only": [pypi_map[v] for v in pypi_only],
        "both": [tag_map[v] for v in both],
    }


def tags_without_release(tags: list[str], release_tags) -> list[str]:
    """Tags that carry no GitHub Release, newest first.

    Answered for every tag from one listing of the releases, not sampled from
    the newest tag: a feature released again after a failure would hide the
    broken version behind the good one.
    """
    published = {_strip_v(t) for t in release_tags}
    return [t for t in tags if _strip_v(t) not in published]


# ── Workspace scanning ────────────────────────────────────────────────


def _repo_of(path: str) -> str | None:
    """org/repo from the origin remote, or None when there is not one."""
    try:
        r = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            cwd=path,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    url = r.stdout.strip()
    if r.returncode != 0 or not url:
        return None
    try:
        return extract_repo(url)
    except SystemExit:
        return None


def discover_packages(
    workspace: str,
    *,
    include_core: bool,
    only: str | None,
    include_products: bool = True,
):
    """Every releasable package in the workspace, as (dirname, path).

    Features, the two core packages, and products. A product publishes to the
    same two channels through the same pipeline, so leaving products out made
    the report silently incomplete for exactly the packages release:resume
    knows how to repair.
    """
    found: list[tuple[str, str]] = []
    for entry in sorted(os.listdir(workspace)):
        path = os.path.join(workspace, entry)
        if entry.startswith(".") or not os.path.isdir(path):
            continue
        if not os.path.isfile(os.path.join(path, "pyproject.toml")):
            continue
        is_feature = entry.startswith("splent_feature_")
        is_core = entry in CORE_PACKAGES
        is_product = not is_feature and not is_core
        if is_core and not include_core:
            continue
        if is_product and not include_products:
            continue
        found.append((entry, path))

    if only:
        wanted = only if only.startswith("splent_") else f"splent_feature_{only}"
        found = [(n, p) for n, p in found if n == wanted or n == only]
    return found


# ── Command ───────────────────────────────────────────────────────────


@click.command(
    "check:releases",
    short_help="Report GitHub/PyPI divergence for every package in the workspace.",
)
@click.option("--feature", "only", default=None, help="Check one package by name.")
@click.option(
    "--no-core",
    "include_core",
    flag_value=False,
    default=True,
    help="Skip splent_cli and splent_framework.",
)
@click.option(
    "--no-products",
    "include_products",
    flag_value=False,
    default=True,
    help="Skip products, check features and core packages only.",
)
@click.option(
    "--divergent-only",
    is_flag=True,
    default=False,
    help="Print only the packages that have a problem.",
)
def check_releases(only, include_core, include_products, divergent_only):
    """Compare git tags against PyPI releases for the whole workspace."""
    workspace = str(context.workspace())
    token = os.getenv("GITHUB_TOKEN")

    packages = discover_packages(
        workspace,
        include_core=include_core,
        only=only,
        include_products=include_products,
    )
    if not packages:
        click.echo()
        click.secho("  No releasable packages found in the workspace.", fg="yellow")
        click.echo()
        return

    click.echo()
    click.secho(f"  Release channels for {len(packages)} package(s)", bold=True)
    click.secho(f"  {'-' * 66}", fg="bright_black")

    tag_not_on_pypi: list[tuple[str, list[str]]] = []
    pypi_not_tagged: list[tuple[str, list[str]]] = []
    tag_without_release: list[tuple[str, list[str]]] = []
    # "Nothing to compare" and "could not compare" are different answers and
    # must never share a bucket: the first is fine, the second means this
    # command does not know, which is not the same as everything being fine.
    no_remote: list[str] = []
    unanswered: list[tuple[str, str]] = []

    def _reason(e: registry.RegistryError, channel: str) -> str:
        if e.rate_limited:
            return f"{channel} is rate limiting this token"
        if e.status:
            return f"{channel} answered HTTP {e.status}"
        return f"{channel} could not be reached ({e})"

    for name, path in packages:
        pyproject = os.path.join(path, "pyproject.toml")
        package = read_project_name(pyproject) or name
        try:
            channels = release_gate.declared_channels(pyproject)
        except release_gate.ChannelDeclarationError as e:
            unanswered.append((name, f"unreadable channel declaration, {e}"))
            click.echo(click.style("  [!] ", fg="yellow") + f"{name}: {e}")
            continue
        repo = _repo_of(path)

        if not repo:
            no_remote.append(name)
            if not divergent_only:
                click.echo(
                    click.style("  [.] ", fg="bright_black")
                    + f"{name}: no origin remote, nothing to compare"
                )
            continue

        org, repo_name = repo.split("/", 1)
        wants_pypi = release_gate.PYPI in channels
        wants_github = release_gate.GITHUB in channels

        try:
            tags = registry.list_semver_tags(org, repo_name, token, quiet=False)
        except registry.RegistryError as e:
            reason = _reason(e, "GitHub")
            unanswered.append((name, reason))
            click.echo(click.style("  [!] ", fg="yellow") + f"{name}: {reason}")
            continue

        try:
            pypi_versions = (
                registry.pypi_versions(package, strict=True) if wants_pypi else []
            )
        except registry.RegistryError as e:
            reason = _reason(e, "PyPI")
            unanswered.append((name, reason))
            click.echo(click.style("  [!] ", fg="yellow") + f"{name}: {reason}")
            continue

        release_tags = None
        if wants_github and tags:
            try:
                release_tags = registry.list_release_tags(org, repo_name, token)
            except registry.RegistryError as e:
                reason = _reason(e, "GitHub") + " when listing releases"
                unanswered.append((name, reason))
                click.echo(click.style("  [!] ", fg="yellow") + f"{name}: {reason}")
                continue

        buckets = compare_channels(tags, pypi_versions)

        problems: list[str] = []
        if wants_pypi and buckets["tag_only"]:
            tag_not_on_pypi.append((name, buckets["tag_only"]))
            problems.append(
                f"{len(buckets['tag_only'])} tag(s) not on PyPI: "
                + ", ".join(buckets["tag_only"][:6])
                + (" ..." if len(buckets["tag_only"]) > 6 else "")
            )
        if wants_pypi and buckets["pypi_only"]:
            pypi_not_tagged.append((name, buckets["pypi_only"]))
            problems.append(
                f"{len(buckets['pypi_only'])} PyPI release(s) with no tag: "
                + ", ".join(buckets["pypi_only"][:6])
                + (" ..." if len(buckets["pypi_only"]) > 6 else "")
            )

        if release_tags is not None:
            missing = tags_without_release(tags, release_tags)
            if missing:
                tag_without_release.append((name, missing))
                problems.append(
                    f"{len(missing)} tag(s) with no GitHub release: "
                    + ", ".join(missing[:6])
                    + (" ..." if len(missing) > 6 else "")
                )

        if problems:
            click.echo(click.style("  [X] ", fg="red") + click.style(name, bold=True))
            for problem in problems:
                click.secho(f"      {problem}", fg="red")
        elif not divergent_only:
            if not wants_pypi:
                click.echo(
                    click.style("  [OK] ", fg="green")
                    + f"{name}: github only by declaration, {len(tags)} tag(s)"
                )
            elif not tags and not pypi_versions:
                click.echo(
                    click.style("  [.] ", fg="bright_black") + f"{name}: never released"
                )
            else:
                click.echo(
                    click.style("  [OK] ", fg="green")
                    + f"{name}: {len(buckets['both'])} version(s) on both channels"
                )

    # ── Summary ───────────────────────────────────────────────────────
    click.echo()
    click.secho(f"  {'-' * 66}", fg="bright_black")

    total = len(tag_not_on_pypi) + len(pypi_not_tagged) + len(tag_without_release)
    if not total and not unanswered:
        checked = len(packages) - len(no_remote)
        click.secho(
            f"  GitHub and PyPI agree for all {checked} package(s) checked.", fg="green"
        )
        if no_remote:
            click.secho(
                f"  {len(no_remote)} package(s) had nothing to compare.",
                fg="bright_black",
            )
        click.echo()
        if not token:
            click.secho(
                "  Set GITHUB_TOKEN to avoid rate limits on this command.", fg="yellow"
            )
            click.echo()
        return

    if tag_not_on_pypi:
        click.secho(
            f"  {len(tag_not_on_pypi)} package(s) tagged but not on PyPI",
            fg="red",
            bold=True,
        )
        for name, versions in tag_not_on_pypi:
            click.echo(f"    {name}  {', '.join(versions)}")
        click.echo()
        click.secho(
            "  Finish the newest one of each without bumping the version with",
            fg="yellow",
        )
        click.echo(f"    splent release:resume {tag_not_on_pypi[0][0]}")
        click.echo()

    if pypi_not_tagged:
        click.secho(
            f"  {len(pypi_not_tagged)} package(s) on PyPI with no tag",
            fg="red",
            bold=True,
        )
        for name, versions in pypi_not_tagged:
            click.echo(f"    {name}  {', '.join(versions)}")
        click.secho(
            "  A published version with no tag has no reviewable source. "
            "Tag the commit it was built from.",
            fg="yellow",
        )
        click.echo()

    if tag_without_release:
        click.secho(
            f"  {len(tag_without_release)} package(s) tagged with no GitHub release",
            fg="red",
            bold=True,
        )
        for name, versions in tag_without_release:
            click.echo(f"    {name}  {', '.join(versions)}")
        click.echo()

    if unanswered:
        click.secho(
            f"  {len(unanswered)} package(s) could NOT be checked, so this report "
            "does not cover them",
            fg="yellow",
            bold=True,
        )
        for name, reason in unanswered:
            click.echo(f"    {name}  {reason}")
        click.secho(
            "  Not knowing is not the same as being in sync. Fix the access and "
            "run this again.",
            fg="yellow",
        )
        click.echo()

    if not token:
        click.secho(
            "  Set GITHUB_TOKEN to avoid rate limits on this command.", fg="yellow"
        )
        click.echo()

    raise SystemExit(1)


cli_command = check_releases
