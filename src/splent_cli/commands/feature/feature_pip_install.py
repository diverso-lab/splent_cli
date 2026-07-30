"""
feature:pip-install installs the declared features into a production image.

Reads [tool.splent].features from the active product's pyproject.toml and
installs each entry. This is what the production image runs;
scripts/00_install_features.sh, which installs the local checkout with
pip install -e, is the development path and never runs here.

PyPI is the preferred source and the git tag is the fallback. Both are the
same artifact: a release pushes the tag and uploads the package built from
that exact commit, so a pin like ``@v0.2.1`` names one immutable thing
whichever channel serves it. Falling back matters because PyPI can refuse to
create a new project (its rate limit is on project creation, not on versions)
and because a private feature may deliberately never be published there.
Without the fallback, one unpublished feature makes the whole product
unbuildable even though its code is tagged and reachable.

Three things about this are worth stating plainly rather than discovering:

  * only [tool.splent].features is read. features_dev and features_prod are
    NOT installed by this command, so a feature declared only under
    features_prod is merged into the deploy env and compose by product:build
    and then never installed into the image.
  * an entry without @version becomes a bare package name, so pip resolves it
    against PyPI and installs whatever is published there. It does not fail,
    it does not install the workspace checkout, and it cannot fall back to a
    git tag because it never named one.
  * the summary says which features came from which channel. An image built
    from git tags is reproducible, but you should know that is what you have.

Example:
    splent-io/splent_feature_auth@v1.2.7  ->  pip install splent_feature_auth==1.2.7
                                          ->  or git+https://github.com/…@v1.2.7
    splent-io/splent_feature_auth         ->  pip install splent_feature_auth
"""

import os
import sys

import click
import tomllib

from splent_cli.services import context
from splent_cli.utils.git_url import https_url, namespace_spellings
from splent_cli.utils.proc import run


def _parse_feature_entry(entry: str) -> tuple[str, str, str | None, str | None]:
    """Parse a [tool.splent].features entry.

    Returns (namespace, package_name, pypi_version, git_ref). The version is
    carried in both spellings on purpose: PyPI wants ``1.2.7`` while the tag
    is ``v1.2.7``, and deriving one from the other in two places is how they
    drift apart.

    'splent-io/splent_feature_auth@v1.2.7' → ('splent-io', 'splent_feature_auth', '1.2.7', 'v1.2.7')
    'splent-io/splent_feature_auth'        → ('splent-io', 'splent_feature_auth', None, None)
    'splent_feature_auth@v1.2.7'           → ('', 'splent_feature_auth', '1.2.7', 'v1.2.7')
    """
    namespace, _, name_ver = entry.rpartition("/")
    name, _, ref = name_ver.partition("@")
    version = ref.lstrip("v") if ref else None
    return namespace, name, version, (ref or None)


def _uninstalled_env_features(splent: dict) -> list[str]:
    """Entries this command will not install because of where they are declared.

    features_dev / features_prod are read by product:build and by
    product:resolve, so it is easy to assume they are read here too. They are
    not, and a feature that is merged into the deploy artifacts but never
    installed into the image fails at import time with nothing pointing back
    at the declaration.
    """
    base = set(splent.get("features", []) or [])
    extra = []
    for key in ("features_prod", "features_dev"):
        for entry in splent.get(key, []) or []:
            if entry not in base:
                extra.append(f"{entry}  [{key}]")
    return extra


def _pypi_does_not_have_it(output: str) -> bool:
    """Did pip fail because PyPI has no such package or version?

    Only that failure justifies reaching for the git tag. A network that is
    down, a wheel that will not build or a dependency conflict would fail the
    same way from git, and retrying would bury the real reason under a second
    error about a different URL.
    """
    lowered = output.lower()
    return any(
        phrase in lowered
        for phrase in (
            "no matching distribution found",
            "could not find a version that satisfies",
            "404 client error",
        )
    )


def _git_candidates(namespace: str, name: str, ref: str) -> list[tuple[str, str]]:
    """Ordered (real, display) pip requirements for the tag, one per spelling.

    The entry carries the Python namespace (``splent_io``) while the repo
    lives under the hosting org (``splent-io``); both are tried, as written
    first. The display form never contains the token.
    """
    candidates = []
    for spelling in namespace_spellings(namespace):
        real, display = https_url(spelling, name)
        candidates.append((f"{name} @ git+{real}@{ref}", f"git+{display}@{ref}"))
    return candidates


def _redacted(text: str) -> str:
    """Never echo the token back, whatever pip decided to print."""
    token = os.getenv("GITHUB_TOKEN")
    return text.replace(token, "***") if token else text


def _pip_install(spec: str) -> tuple[bool, str]:
    result = run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", spec],
        check=False,
        capture=True,
    )
    output = (result.stderr or result.stdout or "").strip()
    return result.returncode == 0, output


def _last_line(output: str) -> str:
    lines = [line for line in output.splitlines() if line.strip()]
    return _redacted(lines[-1]) if lines else "no output"


@click.command(
    "feature:pip-install",
    short_help="Install the declared features from PyPI, or from their git tag.",
)
@click.option(
    "--pypi-only",
    is_flag=True,
    help="Fail instead of falling back to the git tag of a feature PyPI does not serve.",
)
def feature_pip_install(pypi_only):
    """Install features declared in [tool.splent].features.

    Used in production Dockerfiles, where features are installed as published
    packages rather than from local source. PyPI first, the pinned git tag
    second. Only [tool.splent].features is read; anything declared solely
    under features_dev or features_prod is reported and left alone.
    """
    product = context.require_app()
    workspace = str(context.workspace())
    pyproject_path = os.path.join(workspace, product, "pyproject.toml")

    if not os.path.isfile(pyproject_path):
        click.secho("❌ pyproject.toml not found.", fg="red")
        raise SystemExit(1)

    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    splent = data.get("tool", {}).get("splent", {})
    features = splent.get("features", [])

    ignored = _uninstalled_env_features(splent)
    if ignored:
        click.secho(
            "  Declared elsewhere and NOT installed by this command:", fg="yellow"
        )
        for entry in ignored:
            click.echo(f"    {entry}")
        click.secho(
            "  Move them to [tool.splent].features if the image needs them.",
            fg="yellow",
        )
        click.echo()

    if not features:
        click.secho("  No features declared in [tool.splent].features.", fg="yellow")
        return

    click.echo(f"  Installing {len(features)} feature(s)...\n")

    failed = []
    unpinned = []
    from_git = []
    for entry in features:
        namespace, name, version, ref = _parse_feature_entry(entry)
        spec = f"{name}=={version}" if version else name
        if not version:
            unpinned.append(name)

        click.echo(f"  installing {spec}")

        ok, output = _pip_install(spec)
        if ok:
            click.echo(f"  ok {spec}")
            continue

        can_try_git = (
            not pypi_only and ref and namespace and _pypi_does_not_have_it(output)
        )
        if not can_try_git:
            click.secho(f"  FAIL {spec}: {_last_line(output)}", fg="red")
            failed.append(spec)
            continue

        # PyPI has nothing under that name or version, but the release tagged
        # the code, so install what the pin actually names.
        click.secho(
            f"  pypi does not serve {spec}, installing its git tag {ref} instead",
            fg="yellow",
        )
        git_output = ""
        for real_spec, display_spec in _git_candidates(namespace, name, ref):
            ok, git_output = _pip_install(real_spec)
            if ok:
                click.echo(f"  ok {display_spec}")
                from_git.append(f"{name} {ref}")
                break
        if not ok:
            click.secho(
                f"  FAIL {spec}: not on PyPI, and its git tag {ref} could not be "
                f"installed either ({_last_line(git_output)})",
                fg="red",
            )
            failed.append(spec)

    click.echo()
    if unpinned:
        click.secho(
            "  Installed without a version, so pip chose what PyPI serves and "
            "not any local checkout:",
            fg="yellow",
        )
        for name in unpinned:
            click.echo(f"    {name}")
        click.echo()
    if from_git:
        click.secho(
            "  Installed from their git tag because PyPI does not serve them. "
            "A tag is immutable, so the image is still reproducible:",
            fg="yellow",
        )
        for item in from_git:
            click.echo(f"    {item}")
        click.echo()
    if failed:
        click.secho(f"  {len(failed)} feature(s) failed to install:", fg="red")
        for f in failed:
            click.echo(f"    {f}")
        raise SystemExit(1)

    installed_from = "PyPI and git tags" if from_git else "PyPI"
    click.secho(
        f"  All {len(features)} feature(s) installed from {installed_from}.",
        fg="green",
    )


cli_command = feature_pip_install
