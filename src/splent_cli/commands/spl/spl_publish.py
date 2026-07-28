"""
spl:publish — Publish the local UVL model of an SPL through the marketplace.

    developer --(splent CLI, bearer token)--> MARKETPLACE --(one key)--> UVLHub

The developer authenticates to the marketplace with the token `splent login`
stored, sends the .uvl, and gets back the DOI the marketplace recorded. They
never hold a UVLHub key and never need to know UVLHub is on the other side.

This command used to talk to UVLHub directly. It required the developer's own
UVLHUB_API_KEY and, when it was missing, printed "generate one at
{UVLHUB_URL}/developer/api-keys" — instructions for obtaining the exact
credential the marketplace exists to replace. The relay endpoint was live and
validating the whole time and had no client at all, so the loop the two halves
were built to close was never closed. Everything that used to happen here
happens on the server now: the upload, the publish or the new version, the
byte for byte verification of what UVLHub then serves, and the record of what
was minted. What is left here is finding the local model, sending it, and
saying what came back.
"""

import os
import re
import tomllib

import click

from splent_cli.commands.marketplace.marketplace_auth_utils import (
    credential_source_label,
    field,
    handle_error,
    resolve_target,
)
from splent_cli.services import context, credentials, marketplace_api, spl_store
from splent_cli.utils.io_utils import atomic_write, backup_file


# ---------------------------------------------------------------------------
# Finding the local model
# ---------------------------------------------------------------------------


def _candidate_paths(workspace: str, spl_name: str) -> list[str]:
    """Where a copy of the model may live, best first.

    The working copy at ``splent_spl_<name>/`` is what an author edits and is
    therefore what gets published. The cache is second: republishing something
    you only ever consumed is unusual but not wrong, and it is what lets a
    machine that never authored the model still push a fix.
    """
    pin = spl_store.read_pin(workspace, spl_name)
    paths = [
        str(d / spl_store.uvl_filename(spl_name))
        for d in spl_store.working_copy_candidates(workspace, spl_name)
    ]
    paths.extend(
        str(d / spl_store.uvl_filename(spl_name))
        for d in spl_store.cache_candidates(workspace, spl_name, pin.version, pin.doi)
    )
    # De-duplicate while keeping order.
    seen: set[str] = set()
    ordered = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _find_local_uvl(workspace: str, spl_name: str) -> str | None:
    for path in _candidate_paths(workspace, spl_name):
        if os.path.isfile(path):
            return path
    return None


def _metadata_path(workspace: str, spl_name: str) -> str | None:
    """The working copy's metadata.toml, when the model is one being edited.

    Only the working copy is written back to. The cache is derived data and a
    DOI written there would be lost the moment the cache is cleared, which is
    exactly the kind of quiet loss the cache is supposed to be safe from.
    """
    copy = spl_store.working_copy(workspace, spl_name)
    if copy is None:
        return None
    return str(copy / spl_store.METADATA_FILENAME)


def _local_description(metadata_path: str | None) -> str:
    if not metadata_path:
        return ""
    try:
        with open(metadata_path, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return str(data.get("spl", {}).get("description") or "").strip()


# ---------------------------------------------------------------------------
# Keeping the local metadata.toml honest, while it exists
# ---------------------------------------------------------------------------


def _toml_str(value: str) -> str:
    """Render a string as a quoted TOML value."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _update_metadata_uvl(metadata_path: str, *, doi: str, file: str, **extra) -> None:
    """Rewrite [spl.uvl] values preserving the rest of metadata.toml.

    Text-level edit (comments/formatting survive), backed by a .bak copy and
    an atomic write, then re-parsed to confirm the values landed. On any
    failure the original file is restored, so metadata.toml is never left
    corrupted or half-written.

    ``extra`` carries the keys the marketplace may or may not report, notably
    ``concept_doi`` and ``version``. Empty values are skipped rather than
    written blank, so a server that says nothing about the concept DOI never
    erases one that is already recorded.
    """
    updates = {"doi": doi, "file": file}
    updates.update({k: v for k, v in extra.items() if v})

    with open(metadata_path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    section = None
    uvl_header_idx = None
    done: set[str] = set()
    key_pattern = re.compile(rf"^(\s*)({'|'.join(map(re.escape, updates))})\s*=")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            if section == "[spl.uvl]":
                uvl_header_idx = i
            continue
        if section != "[spl.uvl]":
            continue
        m = key_pattern.match(line)
        if not m:
            continue
        key = m.group(2)
        lines[i] = f"{m.group(1)}{key} = {_toml_str(updates[key])}"
        done.add(key)

    if uvl_header_idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[spl.uvl]")
        lines.append('mirror = "uvlhub.io"')
        lines.extend(f"{k} = {_toml_str(v)}" for k, v in updates.items())
    else:
        missing = [f"{k} = {_toml_str(v)}" for k, v in updates.items() if k not in done]
        for offset, entry in enumerate(missing):
            lines.insert(uvl_header_idx + 1 + offset, entry)

    bak = backup_file(metadata_path, ".bak")
    try:
        atomic_write(metadata_path, "\n".join(lines) + "\n")
        with open(metadata_path, "rb") as f:
            written = tomllib.load(f)
        uvl_cfg = written.get("spl", {}).get("uvl", {})
        if any(uvl_cfg.get(k) != v for k, v in updates.items()):
            raise click.ClickException(
                "metadata.toml did not validate after editing "
                "(values not found on re-read)"
            )
    except BaseException:
        if bak is not None:
            atomic_write(metadata_path, bak.read_text(encoding="utf-8"))
        raise
    finally:
        if bak is not None and bak.exists():
            bak.unlink()


# ---------------------------------------------------------------------------
# Reading the marketplace's answer
# ---------------------------------------------------------------------------

#: How the registry spells the outcome of its own byte for byte check.
#: Worded without naming what is behind the marketplace, because a developer
#: publishing through it has no business having to know.
VERIFICATION_WORDING = {
    "match": "the published file matches the model that was sent",
    "mismatch": "the published file DOES NOT match the model that was sent",
    "pending": "not checked by the marketplace",
    "unknown": "could not be checked",
    "imported": "imported, never checked",
}


def _pointer(result: dict) -> tuple[str, str]:
    """(doi, file) from the release response, flat or nested under uvl."""
    uvl = result.get("uvl") if isinstance(result.get("uvl"), dict) else {}
    doi = str(result.get("doi") or uvl.get("doi") or "").strip()
    remote_file = str(uvl.get("file") or result.get("file") or "").strip()
    return doi, remote_file


def _concept_doi(result: dict) -> str:
    """The DOI of the line rather than of this version, when reported.

    UVLHub persists it and it never changes, which is the only reason a
    product pinning v2 can be told the line is on v5 without asking a
    directory service that would have to exist for every deployment.
    """
    uvl = result.get("uvl") if isinstance(result.get("uvl"), dict) else {}
    for source in (result, uvl):
        for key in ("concept_doi", "conceptdoi", "concept"):
            value = source.get(key)
            if value:
                return str(value).strip()
    return ""


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command(
    "spl:publish",
    short_help="Publish the local UVL model through the SPLENT marketplace.",
)
@click.argument("spl_name")
@click.option(
    "--registry",
    "registry_option",
    default=None,
    metavar="URL",
    help="Marketplace to publish to. Defaults to SPLENT_MARKETPLACE_URL, "
    "then https://marketplace.splent.io.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be done without touching the network or metadata.",
)
@context.requires_detached
def spl_publish(spl_name, registry_option, dry_run):
    """Publish the local UVL model of the SPL through the marketplace.

    \b
    The marketplace decides what happens on the other side: a line it has
    never seen becomes a new dataset, one it knows gets a new version of the
    DOI it already holds, and identical bytes it has already published are
    answered with that DOI instead of minting a second one.

    \b
    Requires `splent login`. There is no UVLHub key here and there is not
    meant to be: the marketplace holds one key for everybody.
    """
    workspace = str(context.workspace())
    target = resolve_target(registry_option)
    uvl_path = _find_local_uvl(workspace, spl_name)
    metadata_path = _metadata_path(workspace, spl_name)

    if uvl_path is None:
        click.secho(f"  No local UVL model for '{spl_name}'.", fg="red")
        click.echo("     Looked in:")
        for candidate in _candidate_paths(workspace, spl_name):
            click.echo(f"       {candidate}")
        click.echo(
            "     Build the model first (spl:create / spl:add-feature) or "
            f"download it with: splent spl:fetch {spl_name}"
        )
        raise SystemExit(1)

    try:
        cred = credentials.resolve(target.url)
    except credentials.CredentialsError as e:
        click.secho(f"  {e}", fg="red")
        raise SystemExit(1)

    if dry_run:
        click.echo()
        click.echo(click.style(f"  spl:publish {spl_name} (dry run)", bold=True))
        click.echo()
        field("Local UVL", uvl_path)
        field("Registry", f"{target.url} ({target.origin_label})")
        field(
            "Token",
            credential_source_label(cred) if cred else "(not logged in)",
        )
        click.echo()
        click.echo("  Would POST the model to /api/v1/spls/<name>/releases and:")
        click.echo("    - let the marketplace publish or version it on its own key")
        click.echo("    - let it verify the published file byte for byte")
        click.echo("    - record the DOI it reports")
        if metadata_path:
            click.echo(f"    - write doi/file into {metadata_path}")
        if cred is None:
            click.echo()
            click.secho(
                "  Not logged in, so a real run would fail. Run splent login.",
                fg="yellow",
            )
        click.echo()
        click.secho("  Dry run: nothing was changed.", fg="green")
        return

    if cred is None:
        click.secho(f"  Not logged in to {target.url}", fg="red")
        click.echo("     Run splent login and try again.")
        raise SystemExit(1)

    with open(uvl_path, "rb") as handle:
        local_bytes = handle.read()

    click.echo()
    click.echo(click.style(f"  Publishing {spl_name} to {target.url}", bold=True))
    click.echo()

    client = marketplace_api.MarketplaceClient(target.url, token=cred.token)
    try:
        result = client.publish_spl(
            spl_name,
            local_bytes,
            os.path.basename(uvl_path),
            description=_local_description(metadata_path),
        )
    except marketplace_api.MarketplaceError as e:
        handle_error(e, url=target.url, forget=not cred.from_environment)
        if cred.from_environment and e.dead_token:
            click.secho(
                f"  The token comes from {credentials.TOKEN_ENV}, so unset or "
                "replace it there.",
                fg="yellow",
            )
        click.echo()
        raise SystemExit(1)

    doi, remote_file = _pointer(result)
    concept_doi = _concept_doi(result)
    if not doi:
        click.secho(
            "  The marketplace accepted the release but reported no DOI.", fg="red"
        )
        click.echo("     Check the product line page before publishing again.")
        raise SystemExit(1)

    if result.get("idempotent"):
        click.secho(
            "  This exact model was already published, so nothing was sent on.",
            fg="yellow",
        )
    field("DOI", doi)
    if concept_doi:
        field("Concept DOI", concept_doi)
    version = str(result.get("version") or "").strip()
    if version:
        field("Version", version)
    if remote_file:
        field("File", remote_file)
    verification = str(result.get("verification") or "").strip()
    if verification:
        field("Checked", VERIFICATION_WORDING.get(verification, verification))

    if metadata_path and remote_file:
        _update_metadata_uvl(
            metadata_path,
            doi=doi,
            file=remote_file,
            concept_doi=concept_doi,
            version=version,
        )
        field("Recorded", metadata_path)
    elif remote_file:
        click.echo()
        click.secho(
            f"  Nothing local records this DOI. Products that derive from "
            f"'{spl_name}' need it in their own pyproject.toml, under "
            f"[tool.splent.spl_model].",
            fg="yellow",
        )

    products = spl_store.products_pinning(workspace, spl_name)
    if products:
        click.echo()
        click.echo("  Products pinning this model, none of them updated here:")
        for product, pinned in products:
            click.echo(f"    {product}  {pinned}")
        click.echo("  Move one forward with: splent spl:pin " + spl_name)

    click.echo()
    click.secho(f"  '{spl_name}' published (DOI {doi}).", fg="green")
    click.echo()


cli_command = spl_publish
