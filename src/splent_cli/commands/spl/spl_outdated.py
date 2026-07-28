"""spl:outdated says when a product is deriving from an old model.

Pinning a model by DOI makes a build reproducible, which also means a product
sits on the version it was pinned to forever and never says so. This is the
command that says so.

Two questions, and they are not the same one:

  * Local drift. The workspace already holds a newer model than the product
    pins, because someone published from the working copy here. No network.
  * Remote drift. The line itself has moved on somewhere else. That needs the
    concept DOI, which is exactly why it is recorded.
"""

from __future__ import annotations

import click

from splent_cli.services import context, spl_store


def latest_version_doi(mirror: str, concept_doi: str) -> tuple[str, str] | None:
    """Ask UVLHub what the newest version of a line is.

    Returns ``(version_doi, version_label)`` or None when the answer cannot be
    read. Best-effort by design: a concept DOI resolves to a landing page
    whose machine-readable shape is not part of any contract the CLI controls,
    so a shape it does not recognise is reported as "could not tell" and never
    as "you are up to date".
    """
    from splent_cli.commands.uvl.uvl_utils import uvlhub_base_url

    if mirror != spl_store.DEFAULT_MIRROR or not concept_doi:
        return None

    import requests

    url = f"{uvlhub_base_url()}/doi/{concept_doi}/"
    try:
        response = requests.get(url, timeout=15, headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise click.ClickException(f"Could not reach {url}: {exc}")

    if response.status_code != 200:
        raise click.ClickException(f"UVLHub returned {response.status_code} for {url}")

    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    doi = ""
    for key in ("doi", "version_doi", "latest_doi"):
        if payload.get(key):
            doi = str(payload[key]).strip()
            break
    if not doi or doi == concept_doi:
        return None

    version = ""
    for key in ("version", "version_label", "latest_version"):
        if payload.get(key):
            version = str(payload[key]).strip()
            break
    return doi, version


def _report(product: str, pin: spl_store.SplPin, message: str, colour: str) -> None:
    click.secho(f"  {product:<24} {message}", fg=colour)


@click.command(
    "spl:outdated",
    short_help="Report products pinned to an older SPL model than exists.",
)
@click.option(
    "--remote",
    is_flag=True,
    help="Also ask UVLHub whether the line itself has moved on.",
)
@context.requires_detached
def spl_outdated(remote):
    """Compare what each product pins against what exists."""
    workspace = str(context.workspace())
    names = spl_store.known_spls(workspace)

    click.echo()
    click.secho("  spl:outdated", bold=True)
    click.echo()

    if not names:
        click.secho("  No SPL models known to this workspace.", fg="yellow")
        click.echo()
        return

    behind = 0
    unknown = 0
    checked = 0

    for name in names:
        products = spl_store.products_pinning(workspace, name)
        if not products:
            continue

        available = spl_store.read_pin(workspace, name)
        latest = None
        if remote and available.concept_doi:
            latest = latest_version_doi(available.mirror, available.concept_doi)

        click.secho(f"  {name}", bold=True)
        for product, _ in products:
            checked += 1
            pin = spl_store.read_product_pin(workspace, product)
            if pin is None or not pin.doi:
                _report(product, pin, "no DOI recorded, run splent spl:pin", "yellow")
                unknown += 1
                continue

            if latest and latest[0] != pin.doi:
                label = latest[1] or latest[0]
                _report(
                    product,
                    pin,
                    f"pins {pin.version or pin.doi}, the line is on {label}",
                    "yellow",
                )
                behind += 1
                continue

            if available.doi and available.doi != pin.doi:
                _report(
                    product,
                    pin,
                    f"pins {pin.version or pin.doi}, this workspace holds "
                    f"{available.version or available.doi}",
                    "yellow",
                )
                behind += 1
                continue

            if remote and not available.concept_doi:
                _report(
                    product,
                    pin,
                    f"pins {pin.version or pin.doi}, no concept DOI so the line "
                    "cannot be checked",
                    "bright_black",
                )
                unknown += 1
                continue

            if remote and available.concept_doi and latest is None:
                _report(
                    product,
                    pin,
                    f"pins {pin.version or pin.doi}, UVLHub did not say what the "
                    "newest version is",
                    "bright_black",
                )
                unknown += 1
                continue

            _report(product, pin, f"pins {pin.version or pin.doi}, up to date", "green")
        click.echo()

    if not checked:
        click.secho("  No product pins an SPL model.", fg="yellow")
        click.echo()
        return

    summary = f"  {checked} product(s) checked, {behind} behind"
    if unknown:
        summary += f", {unknown} could not be determined"
    click.secho(summary, fg="yellow" if behind else "green")
    if not remote:
        click.secho(
            "  Only this workspace was consulted. Add --remote to ask UVLHub.",
            dim=True,
        )
    click.echo()


cli_command = spl_outdated
