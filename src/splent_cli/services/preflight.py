"""
Pre-flight checks shared across product commands (derive, build, deploy).

Returns True if all checks pass, False otherwise.
"""

import os

import click

from splent_cli.services import context
from splent_cli.utils.feature_utils import read_features_from_data
from splent_cli.utils.io_utils import load_toml


def _check_pypi_exists(package: str, version: str) -> bool:
    """Check if a package@version exists on PyPI."""
    import requests

    # Strip leading 'v' from version for PyPI (v1.0.0 → 1.0.0)
    pypi_version = version.lstrip("v")
    url = f"https://pypi.org/pypi/{package}/{pypi_version}/json"
    try:
        r = requests.head(url, timeout=5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _check_tag_exists(namespace: str, repo: str, tag: str) -> bool:
    """Is the pinned version tagged on the hosting side?

    Asked without cloning, and without ever letting git stop to ask for
    credentials, since a wrong namespace spelling over HTTPS is answered with
    a username prompt rather than an error.
    """
    from splent_cli.utils.git_url import (
        _non_interactive_env,
        https_url,
        namespace_spellings,
    )
    from splent_cli.utils.proc import run

    for spelling in namespace_spellings(namespace):
        real, _ = https_url(spelling, repo)
        result = run(
            ["git", "ls-remote", "--tags", real, f"refs/tags/{tag}"],
            check=False,
            capture=True,
            env=_non_interactive_env(),
        )
        if result.returncode == 0 and (result.stdout or "").strip():
            return True
    return False


def _check_features_ready(workspace: str, product_dir: str, interactive: bool) -> bool:
    """Check that every prod feature is versioned and can reach the image.

    This is not a formality. The production image installs features with pip
    (``splent feature:pip-install`` in the builder stage of
    ``Dockerfile.<product>.prod``), so a feature neither channel serves cannot
    get into the image, and one declared without a version is installed as a
    bare package name, meaning pip serves whatever PyPI has rather than the
    code in this workspace.

    PyPI is not the only channel. A release tags the commit and uploads the
    package built from it, so the tag is the same artifact and pip-install
    falls back to it. Refusing to build over a missing PyPI project would
    block a product whose code is tagged and reachable, which is what
    happened when PyPI rate-limited the creation of new projects.

    Returns True if all features pass.
    """
    pyproject_path = os.path.join(product_dir, "pyproject.toml")
    data = load_toml(pyproject_path, what="pyproject.toml")

    features = read_features_from_data(data, "prod")
    if not features:
        return True

    issues = []
    from_tag = []
    for entry in features:
        namespace = entry.split("/")[0] if "/" in entry else ""
        name = entry.split("/")[-1] if "/" in entry else entry
        bare_name = name.split("@")[0]
        short = bare_name.replace("splent_feature_", "")

        # Check versioned
        if "@" not in name:
            issues.append(
                (
                    short,
                    "no version, so the image would install whatever PyPI has "
                    "under that name, and there is no tag to fall back to. "
                    "Release it first",
                )
            )
            continue

        version = name.split("@")[1]

        if _check_pypi_exists(bare_name, version):
            continue

        # Not on PyPI. The tag is the other half of the same release, so the
        # build still works, but say which features arrive that way.
        if namespace and _check_tag_exists(namespace, bare_name, version):
            from_tag.append((short, version))
        else:
            issues.append(
                (
                    short,
                    f"@{version} is on neither channel, not on PyPI and not "
                    "tagged on GitHub, so the image cannot install it",
                )
            )

    if interactive:
        for short, version in from_tag:
            click.secho(
                f"  features  {short}: not on PyPI, the image will install its "
                f"git tag {version}",
                fg="yellow",
            )

    if not issues:
        if interactive:
            source = "on PyPI" if not from_tag else "on PyPI or tagged"
            click.echo(
                f"  features  all {len(features)} feature(s) versioned and {source}"
            )
        return True

    if interactive:
        for short, problem in issues:
            click.secho(f"  features  {short}: {problem}", fg="red")
    return False


def run_preflight(*, interactive: bool = True, build_mode: bool = False) -> bool:
    """
    Run product:validate as pre-flight check.

    Parameters
    ----------
    interactive : bool
        When True, prints output. When False, silent (returns result only).
    build_mode : bool
        When True, additionally checks that all prod features are versioned
        and available (for product:build / product:deploy).

    Returns True if all checks pass.
    """
    workspace = str(context.workspace())
    product = context.require_app()
    product_dir = os.path.join(workspace, product)

    # Run the full product:validate programmatically
    from splent_cli.commands.uvl.uvl_utils import (
        read_splent_app as _read_splent_app,
        load_pyproject as _load_pyproject,
    )
    from splent_cli.commands.product.product_validate import (
        _run_sat_check,
        _run_compat_check,
    )

    app_name = _read_splent_app(workspace=workspace)
    pyproject_path = os.path.join(product_dir, "pyproject.toml")
    data = _load_pyproject(pyproject_path)

    failed = False

    # Phase 0: pyproject sanity (duplicates, namespaces, SPL)
    splent_cfg = data.get("tool", {}).get("splent", {})
    base_feats = splent_cfg.get("features", [])
    dev_feats = splent_cfg.get("features_dev", [])
    prod_feats = splent_cfg.get("features_prod", [])

    all_entries = base_feats + dev_feats + prod_feats
    seen = {}
    for entry in all_entries:
        bare = (
            entry.split("/")[-1].split("@")[0] if "/" in entry else entry.split("@")[0]
        )
        seen.setdefault(bare, []).append(entry)

    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    if duplicates:
        if interactive:
            for bare, entries in duplicates.items():
                short = bare.replace("splent_feature_", "")
                click.secho(
                    f"  pyproject duplicate '{short}': {', '.join(entries)}", fg="red"
                )
        failed = True
    else:
        namespaces = set()
        for entry in all_entries:
            if "/" in entry:
                namespaces.add(entry.split("/")[0])
        if len(namespaces) > 1:
            if interactive:
                click.secho(
                    f"  pyproject inconsistent namespaces: {', '.join(sorted(namespaces))}",
                    fg="yellow",
                )

    spl_name = splent_cfg.get("spl")
    if spl_name:
        from splent_cli.services import spl_store

        # Allowed to fetch: a fresh clone has the DOI in its pyproject and
        # nothing on disk, and that has to be enough to derive.
        if not spl_store.product_uvl(workspace, product, allow_fetch=True, quiet=False):
            if interactive:
                click.secho(
                    f"  pyproject SPL '{spl_name}' has no model available", fg="red"
                )
                for line in spl_store.missing_model_message(
                    workspace, spl_name
                ).splitlines():
                    click.secho(f"           {line}", fg="bright_black")
            failed = True

    if failed:
        return False

    # Phase 1: SAT
    try:
        sat_ok, selected, _, _ = _run_sat_check(workspace, app_name, data, None, False)
    except Exception as exc:
        # A crash in the SAT checker is NOT a clean "not satisfiable" result —
        # surface it so a broken checker is never reported as a normal failure.
        if interactive:
            click.secho(f"  validate SAT check crashed: {exc}", fg="red")
            click.secho(
                "           run 'splent product:validate' to inspect", fg="bright_black"
            )
        failed = True
    else:
        if sat_ok:
            if interactive:
                click.echo("  validate UVL configuration is satisfiable")
        else:
            if interactive:
                click.secho("  validate UVL configuration is NOT satisfiable", fg="red")
                click.secho(
                    "           run 'splent product:validate' to inspect",
                    fg="bright_black",
                )
            failed = True

    # Phase 2: Contracts
    try:
        findings, errors, warnings = _run_compat_check(workspace, product_dir)
    except Exception as exc:
        # An empty `errors` list reads as a clean PASS — a checker crash must
        # never be swallowed into that. Treat the crash as a failure and say so.
        if interactive:
            click.secho(f"  contract checker crashed: {exc}", fg="red")
            click.secho(
                "           run 'splent product:validate' to inspect",
                fg="bright_black",
            )
        failed = True
    else:
        if not errors:
            if interactive:
                if warnings:
                    click.echo(
                        f"  contract no conflicts — {len(warnings)} warning(s), "
                        "run 'splent product:validate' to review"
                    )
                else:
                    click.echo("  contract no conflicts detected")
        else:
            if interactive:
                for err in errors:
                    click.secho(
                        f"  contract [{err['field']}] {err['message']}", fg="red"
                    )
                click.secho(
                    "           run 'splent product:validate' to inspect",
                    fg="bright_black",
                )
            failed = True

    # Phase 3: Feature readiness (build/deploy only)
    if build_mode:
        if not _check_features_ready(workspace, product_dir, interactive):
            failed = True

    return not failed
