"""
marketplace:index — Build the marketplace index from the real sources.

The index is a regenerable cache: every entry derives from a feature's
[tool.splent.contract] (read at its latest released tag on GitHub, or from
the workspace in --local mode) plus the SPL catalog. Publishing a feature
is `splent feature:release` — the tag IS the publication; this command just
picks it up. There is no server, no upload, and nothing to keep in sync by
hand: when in doubt, rebuild.
"""

import os
from pathlib import Path

import click

from splent_cli.services import context, marketplace, registry
from splent_cli.utils.io_utils import load_toml


def _sources_from_registry_file(path: str) -> tuple[list[str], list[str]]:
    """Read orgs/repos from a registry.toml ([registry] orgs = [...], repos = [...]).

    This file is the curation point for third-party features: external
    authors get listed by adding their org or repo here (PR-reviewed),
    never by pushing data into the marketplace.
    """
    data = load_toml(path, what="registry file")
    reg = data.get("registry", {})
    orgs = [o for o in reg.get("orgs", []) if isinstance(o, str)]
    repos = [r for r in reg.get("repos", []) if isinstance(r, str) and "/" in r]
    return orgs, repos


@click.command(
    "marketplace:index",
    short_help="Build the marketplace index from feature contracts and the SPL catalog.",
)
@click.option(
    "--org",
    "orgs",
    multiple=True,
    help="GitHub organisation to index (repeatable). Default: splent-io.",
)
@click.option(
    "--repo",
    "repos",
    multiple=True,
    help="Extra org/repo to index explicitly (repeatable, for third-party features).",
)
@click.option(
    "--registry",
    "registry_file",
    default=None,
    help="registry.toml listing orgs/repos to index (curated source list).",
)
@click.option(
    "--local",
    is_flag=True,
    help="Index the editable features in the workspace instead of GitHub (no network).",
)
@click.option(
    "--output",
    "output",
    default=None,
    help="Write the index to this path (default: .splent_cache/marketplace/index.json).",
)
@click.option(
    "--no-pypi",
    is_flag=True,
    help="Skip the PyPI publication check (faster, fewer network calls).",
)
def marketplace_index(orgs, repos, registry_file, local, output, no_pypi):
    """
    Build index.json for the SPLENT marketplace.

    \b
    The index is derived from:
      - [tool.splent.contract] of each feature at its latest released tag
        (or the workspace pyprojects with --local)
      - every SPL model the workspace knows: UVL structure and constraints
      - computed relations: used_by (reverse deps) and provides collisions

    \b
    Examples:
      splent marketplace:index                     # index splent-io from GitHub
      splent marketplace:index --local             # index the workspace (offline)
      splent marketplace:index --org my-org --repo other-org/splent_feature_x
      splent marketplace:index --registry registry.toml --output ./index.json
    """
    workspace = str(context.workspace())
    token = registry.github_token()

    org_list = list(orgs)
    repo_list = list(repos)
    if registry_file:
        reg_orgs, reg_repos = _sources_from_registry_file(registry_file)
        org_list.extend(o for o in reg_orgs if o not in org_list)
        repo_list.extend(r for r in reg_repos if r not in repo_list)

    click.echo()
    click.echo(click.style("  marketplace:index", bold=True))
    click.echo(click.style(f"  {'─' * 60}", fg="bright_black"))

    problems: list[str] = []

    if local:
        click.echo("  source   workspace (editable features)")
        features = marketplace.build_workspace_features(workspace)
    else:
        if not org_list:
            org_list = [os.getenv("SPLENT_DEFAULT_ORG", "splent-io")]
        click.echo(f"  source   GitHub: {', '.join(org_list)}")
        if repo_list:
            click.echo(f"  extra    {', '.join(repo_list)}")
        if not token:
            click.secho(
                "  💡 No GITHUB_TOKEN set — rate limits may apply.", fg="yellow"
            )

        def _progress(msg: str):
            click.echo(click.style(f"  index    {msg}", dim=True))

        try:
            features, problems = marketplace.build_remote_features(
                org_list,
                repo_list,
                token=token,
                check_pypi=not no_pypi,
                progress=_progress,
            )
        except registry.RegistryError as e:
            # Abort BEFORE writing anything: a partial build must never
            # overwrite a good index.
            if e.rate_limited:
                click.secho("  ❌ GitHub API rate limit exceeded.", fg="red")
            elif e.status == 403:
                click.secho("  ❌ GitHub API access forbidden (HTTP 403).", fg="red")
            else:
                click.secho(f"  ❌ {e}.", fg="red")
            if not token:
                click.secho(
                    "  💡 Set GITHUB_TOKEN to raise your rate limit and access "
                    "private repos.",
                    fg="yellow",
                )
            click.secho("  Index NOT written.", fg="red")
            raise SystemExit(1)

    spls = marketplace.build_spls(workspace)

    index = marketplace.assemble_index(
        features,
        spls,
        sources={
            "orgs": org_list if not local else [],
            "repos": repo_list if not local else [],
            "workspace": local,
        },
    )

    target = Path(output) if output else marketplace.index_cache_path(workspace)
    marketplace.save_index(index, target)

    click.echo()
    click.echo(f"  features   {len(index['features'])}")
    click.echo(f"  spls       {len(index['spls'])}")
    if index["collisions"]:
        click.secho(
            f"  collisions {len(index['collisions'])} "
            "(features providing the same route/service/model)",
            fg="yellow",
        )
        for c in index["collisions"]:
            click.echo(
                click.style(
                    f"    {c['kind']}: {c['item']} ← {', '.join(c['features'])}",
                    fg="yellow",
                )
            )
    if problems:
        click.echo()
        click.secho(
            f"  ⚠  {len(problems)} source(s) could not be indexed:", fg="yellow"
        )
        for p in problems:
            click.echo(click.style(f"    - {p}", fg="yellow"))

    click.echo()
    click.secho(f"  ✅ Index written to {target}", fg="green")
    click.echo()


cli_command = marketplace_index
