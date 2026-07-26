"""
spl:publish — Publish the local UVL model of an SPL to UVLHub.

Closes the UVL → UVLHub → spl:fetch cycle: the .uvl files are not tracked in
git (UVLHub is the remote source of truth), so after editing a model locally
this command uploads it, publishes it (new dataset or new version of the
existing DOI), verifies the published raw file matches the local one
byte-for-byte, and only then records the new doi/file in metadata.toml.
"""

import os
import re
import tomllib

import click
import requests

from splent_cli.commands.spl.spl_utils import _resolve_spl_metadata
from splent_cli.commands.uvl.uvl_utils import (
    resolve_uvlhub_raw_url,
    uvlhub_base_url,
)
from splent_cli.services import context
from splent_cli.utils.io_utils import atomic_write, backup_file

_TIMEOUT = 60


# ---------------------------------------------------------------------------
# UVLHub API boundary (all network I/O goes through these helpers)
# ---------------------------------------------------------------------------


def _api_base() -> str:
    """Base URL of the UVLHub instance (UVLHUB_URL env, default uvlhub.io).

    Shared with resolve_uvlhub_raw_url so upload/publish and verification
    always target the SAME instance.
    """
    return uvlhub_base_url()


def _server_message(response) -> str:
    """Extract a human-readable error message from an API response."""
    try:
        data = response.json()
    except Exception:  # noqa: BLE001 - any body that is not JSON
        data = None
    if isinstance(data, dict):
        for key in ("error", "message", "detail"):
            if data.get(key):
                return str(data[key])
    text = (getattr(response, "text", "") or "").strip()
    return text[:300] if text else f"HTTP {response.status_code}"


def _check_response(response, action: str) -> dict:
    """Fail with the server's message on non-2xx; return the JSON body."""
    if response.status_code not in (200, 201):
        raise click.ClickException(
            f"UVLHub rejected the {action} ({response.status_code}): "
            f"{_server_message(response)}"
        )
    try:
        data = response.json()
    except Exception:  # noqa: BLE001
        raise click.ClickException(
            f"UVLHub returned an invalid response for the {action} (not JSON)"
        )
    if not isinstance(data, dict):
        raise click.ClickException(
            f"UVLHub returned an unexpected response for the {action}"
        )
    return data


def _api_upload(base: str, api_key: str, uvl_path: str, title: str, description: str):
    """POST /api/v1/datasets/upload — create a draft dataset with the UVL."""
    try:
        with open(uvl_path, "rb") as fh:
            r = requests.post(
                f"{base}/api/v1/datasets/upload",
                headers={"X-API-Key": api_key},
                data={"title": title, "description": description},
                files={"uvl_file": (os.path.basename(uvl_path), fh)},
                timeout=_TIMEOUT,
            )
    except requests.RequestException as e:
        raise click.ClickException(f"Could not reach UVLHub at {base}: {e}")
    return _check_response(r, "upload")


def _api_publish(base: str, api_key: str, dataset_id) -> dict:
    """POST /api/v1/datasets/<id>/publish — publish the draft (mints a DOI)."""
    try:
        r = requests.post(
            f"{base}/api/v1/datasets/{dataset_id}/publish",
            headers={"X-API-Key": api_key},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise click.ClickException(f"Could not reach UVLHub at {base}: {e}")
    return _check_response(r, "publish")


def _api_lookup_doi(base: str, api_key: str, doi: str) -> dict:
    """GET /api/v1/datasets/doi/<doi> — resolve a DOI to its dataset."""
    try:
        r = requests.get(
            f"{base}/api/v1/datasets/doi/{doi}",
            headers={"X-API-Key": api_key},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise click.ClickException(f"Could not reach UVLHub at {base}: {e}")
    return _check_response(r, "DOI lookup")


def _api_new_version(base: str, api_key: str, dataset_id, uvl_path: str) -> dict:
    """POST /api/v1/datasets/<id>/new-version — publish a new version."""
    try:
        with open(uvl_path, "rb") as fh:
            r = requests.post(
                f"{base}/api/v1/datasets/{dataset_id}/new-version",
                headers={"X-API-Key": api_key},
                files={"file": (os.path.basename(uvl_path), fh)},
                timeout=_TIMEOUT,
            )
    except requests.RequestException as e:
        raise click.ClickException(f"Could not reach UVLHub at {base}: {e}")
    return _check_response(r, "new-version")


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _pick_remote_file(files, local_name: str) -> tuple[str, str | None]:
    """Return (name, raw_url) of the REAL remote file from the API's files list.

    UVLHub may rename the upload (secure_filename / collision suffixes), so
    the name the server reports is the one metadata.toml must point at. The
    raw_url the server reports (built with the host that actually served the
    publish) is the authoritative download location for verification.
    """
    if not isinstance(files, list) or not files:
        raise click.ClickException(
            "UVLHub response did not include the published files"
        )
    entries = [f for f in files if isinstance(f, dict) and f.get("name")]
    if not entries:
        raise click.ClickException(
            "UVLHub response did not include the published file name"
        )
    chosen = next(
        (f for f in entries if f["name"] == local_name),
        next((f for f in entries if f["name"].endswith(".uvl")), entries[0]),
    )
    raw_url = chosen.get("raw_url")
    return chosen["name"], raw_url if isinstance(raw_url, str) else None


def _toml_str(value: str) -> str:
    """Render a string as a quoted TOML value."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _update_metadata_uvl(metadata_path: str, *, doi: str, file: str) -> None:
    """Rewrite doi/file inside [spl.uvl] preserving the rest of metadata.toml.

    Text-level edit (comments/formatting survive), backed by a .bak copy and
    an atomic write, then re-parsed to confirm the values landed. On any
    failure the original file is restored — metadata.toml is never left
    corrupted or half-written.
    """
    with open(metadata_path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    section = None
    uvl_header_idx = None
    doi_done = file_done = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            if section == "[spl.uvl]":
                uvl_header_idx = i
            continue
        if section != "[spl.uvl]":
            continue
        m = re.match(r"^(\s*)(doi|file)\s*=", line)
        if not m:
            continue
        key = m.group(2)
        value = doi if key == "doi" else file
        lines[i] = f"{m.group(1)}{key} = {_toml_str(value)}"
        if key == "doi":
            doi_done = True
        else:
            file_done = True

    if uvl_header_idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("[spl.uvl]")
        lines.append('mirror = "uvlhub.io"')
        lines.append(f"doi = {_toml_str(doi)}")
        lines.append(f"file = {_toml_str(file)}")
    else:
        missing = []
        if not doi_done:
            missing.append(f"doi = {_toml_str(doi)}")
        if not file_done:
            missing.append(f"file = {_toml_str(file)}")
        for offset, entry in enumerate(missing):
            lines.insert(uvl_header_idx + 1 + offset, entry)

    bak = backup_file(metadata_path, ".bak")
    try:
        atomic_write(metadata_path, "\n".join(lines) + "\n")
        with open(metadata_path, "rb") as f:
            written = tomllib.load(f)
        uvl_cfg = written.get("spl", {}).get("uvl", {})
        if uvl_cfg.get("doi") != doi or uvl_cfg.get("file") != file:
            raise click.ClickException(
                "metadata.toml did not validate after editing "
                "(doi/file not found on re-read)"
            )
    except BaseException:
        if bak is not None:
            atomic_write(metadata_path, bak.read_text(encoding="utf-8"))
        raise
    finally:
        if bak is not None and bak.exists():
            bak.unlink()


def _verify_published(
    mirror: str,
    doi: str,
    remote_file: str,
    local_bytes: bytes,
    raw_url: str | None = None,
):
    """Download the published raw file and compare it byte-for-byte.

    Prefers the absolute raw_url reported by the server (it carries the host
    that actually served the publish); only falls back to the URL built from
    mirror/doi/file. Follows redirects (the DOI mapping may 302 to the
    versioned dataset). Returns (matches: bool, url: str).
    """
    if raw_url and raw_url.startswith(("http://", "https://")):
        url = raw_url
    else:
        url = resolve_uvlhub_raw_url(mirror, doi, remote_file)
    try:
        r = requests.get(url, timeout=_TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        raise click.ClickException(f"Verification download failed: {e}")
    if r.status_code != 200:
        raise click.ClickException(
            f"Verification failed: UVLHub returned {r.status_code} for {url}"
        )
    return r.content == local_bytes, url


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command(
    "spl:publish",
    short_help="Publish the local UVL model to UVLHub and update metadata.toml",
)
@click.argument("spl_name")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be done without touching the network or metadata.",
)
@context.requires_detached
def spl_publish(spl_name, dry_run):
    """Publish the local UVL model of the SPL to UVLHub.

    \b
    - Empty doi in metadata.toml → uploads a NEW dataset and publishes it.
    - Existing doi → publishes a NEW VERSION of that dataset.

    After publishing, the raw file on UVLHub is downloaded and compared
    byte-for-byte with the local UVL; only if they match are doi/file
    written to splent_catalog/{spl}/metadata.toml.

    Requires UVLHUB_API_KEY (generate one at {UVLHUB_URL}/developer/api-keys).
    """
    workspace = str(context.workspace())
    base = _api_base()

    metadata = _resolve_spl_metadata(spl_name)
    spl_cfg = metadata.get("spl", {})
    uvl_cfg = spl_cfg.get("uvl", {})
    mirror = uvl_cfg.get("mirror") or "uvlhub.io"
    doi = (uvl_cfg.get("doi") or "").strip()

    spl_dir = os.path.join(workspace, "splent_catalog", spl_name)
    metadata_path = os.path.join(spl_dir, "metadata.toml")
    uvl_path = os.path.join(spl_dir, f"{spl_name}.uvl")

    if mirror != "uvlhub.io":
        click.secho(
            f"  ❌ Unsupported mirror '{mirror}' in metadata.toml "
            f"(only 'uvlhub.io' can be published to).",
            fg="red",
        )
        raise SystemExit(1)

    if not os.path.isfile(uvl_path):
        click.secho(f"  ❌ Local UVL not found: {uvl_path}", fg="red")
        click.echo(
            "     Nothing to publish. Build the model first "
            "(spl:create / spl:add-feature)"
        )
        click.echo(
            f"     or download the current version with: splent spl:fetch {spl_name}"
        )
        raise SystemExit(1)

    title = spl_cfg.get("name") or spl_name
    description = (spl_cfg.get("description") or "").strip() or (
        f"UVL variability model for the '{spl_name}' SPLENT product line."
    )

    if dry_run:
        click.echo()
        click.echo(click.style(f"  spl:publish {spl_name} (dry run)", bold=True))
        click.echo()
        click.echo(f"  Local UVL : {uvl_path}")
        click.echo(f"  API base  : {base}")
        click.echo()
        if doi:
            click.echo(f"  Would publish a NEW VERSION of the existing DOI {doi}:")
            click.echo("    1. GET  /api/v1/datasets/doi/… (resolve dataset id)")
            click.echo("    2. POST /api/v1/datasets/<id>/new-version (upload UVL)")
        else:
            click.echo("  Would upload a NEW dataset and publish it:")
            click.echo(f"    1. POST /api/v1/datasets/upload (title: {title})")
            click.echo("    2. POST /api/v1/datasets/<id>/publish (mints the DOI)")
        click.echo(
            "    3. Verify the published raw file matches the local UVL "
            "byte-for-byte"
        )
        click.echo(f"    4. Write doi/file into {metadata_path}")
        if not os.getenv("UVLHUB_API_KEY"):
            click.echo()
            click.secho(
                "  ⚠ UVLHUB_API_KEY is not set — a real run would fail.",
                fg="yellow",
            )
        click.echo()
        click.secho("  ✅ Dry run: nothing was changed.", fg="green")
        return

    api_key = os.getenv("UVLHUB_API_KEY")
    if not api_key:
        click.secho("  ❌ UVLHUB_API_KEY is not set.", fg="red")
        click.echo(f"     Generate an API key at {base}/developer/api-keys")
        click.echo("     and export it before publishing:")
        click.echo("       export UVLHUB_API_KEY=<your-key>")
        raise SystemExit(1)

    with open(uvl_path, "rb") as f:
        local_bytes = f.read()
    local_name = os.path.basename(uvl_path)

    click.echo()
    click.echo(click.style(f"  Publishing {spl_name} to {base}", bold=True))
    click.echo()

    if doi:
        click.echo(f"  Existing DOI {doi} — creating a new version...")
        dataset = _api_lookup_doi(base, api_key, doi)
        dataset_id = dataset.get("dataset_id")
        if not dataset_id:
            raise click.ClickException(
                f"UVLHub DOI lookup for {doi} did not return a dataset_id"
            )
        result = _api_new_version(base, api_key, dataset_id, uvl_path)
    else:
        click.echo("  No DOI in metadata.toml — uploading a new dataset...")
        uploaded = _api_upload(base, api_key, uvl_path, title, description)
        dataset_id = uploaded.get("dataset_id")
        if not dataset_id:
            raise click.ClickException(
                "UVLHub upload did not return a dataset_id"
            )
        click.echo(f"  📤 Draft dataset created (id {dataset_id}) — publishing...")
        result = _api_publish(base, api_key, dataset_id)

    new_doi = (result.get("doi") or "").strip()
    if not new_doi:
        raise click.ClickException("UVLHub did not return a DOI after publishing")
    remote_file, raw_url = _pick_remote_file(result.get("files"), local_name)
    click.echo(f"  🌐 Published as DOI {new_doi} (file: {remote_file})")

    click.echo("  Verifying published content against the local UVL...")
    matches, url = _verify_published(
        mirror, new_doi, remote_file, local_bytes, raw_url
    )
    if not matches:
        click.secho(
            "  ❌ Verification failed: the published content differs from "
            "the local UVL.",
            fg="red",
        )
        click.echo(f"     URL: {url}")
        click.echo(
            "     metadata.toml was NOT modified — it still points to the "
            "previous version."
        )
        raise SystemExit(1)

    _update_metadata_uvl(metadata_path, doi=new_doi, file=remote_file)

    click.echo()
    click.secho(
        f"  ✅ '{spl_name}' published to UVLHub (DOI {new_doi}).", fg="green"
    )
    if doi and new_doi != doi:
        click.echo(f"     metadata.toml updated: doi {doi} → {new_doi}")
    click.echo()


cli_command = spl_publish
