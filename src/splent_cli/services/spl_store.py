"""Where SPL models live, and how a name becomes a UVL file on disk.

This module replaces ``splent_catalog``. The catalog was a fourth git
repository whose only job was to map an SPL name to a DOI, which made it a
hard dependency of every workspace and of every deployment that had to
resolve a model.

Three places hold a model now, and they are tried in this order:

1. The working copy, ``<workspace>/splent_spl_<name>/``. This is the
   developer's own repository, the mirror image of ``splent_feature_<name>/``.
   It is where a model is edited and where a model that has never been
   published lives. It is never written to by the resolver.
2. The cache, ``<workspace>/.splent_cache/spls/<name>@<version or DOI>/``.
   This mirrors ``.splent_cache/features/<org>/<feature>@<version>``. It is
   derived data: deleting it costs one download and nothing else. The leaf is
   keyed on the version when the pin records one and on the DOI otherwise,
   never on the name alone, so two products pinning two versions of one SPL
   never share a directory.
3. UVLHub, by DOI. Whatever comes down is written into the cache, so the
   next resolution and every resolution after it works offline.

The bootstrap problem and its answer
------------------------------------
A product declares only the NAME of its SPL, and a name cannot be turned
into a download without a directory somewhere that maps names to DOIs. That
directory was the catalog, and it would have to be reachable from every
deployment, including the deployment of the marketplace itself, which is
circular.

So the product records the DOI alongside the name, in its own
``pyproject.toml``:

.. code-block:: toml

    [tool.splent]
    spl = "cms_spl"

    [tool.splent.spl_model]
    mirror = "uvlhub.io"
    doi = "10.5281/zenodo.21610307"
    concept_doi = "10.5281/zenodo.21610306"
    version = "v2"

``doi`` pins the exact bytes, the way ``org/repo@version`` pins a feature.
``concept_doi`` is the DOI of the line rather than of one version. UVLHub
persists it and it never changes, so :func:`read_pin` gives ``spl:outdated``
enough to say "you pin v2, the line is on v5" without a directory service.

With that, deriving a product needs the product's own repository and UVLHub.
It never needs the marketplace and never needs this workspace to hold any
particular other repository.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import click

from splent_cli.utils.io_utils import atomic_write

WORKING_COPY_PREFIX = "splent_spl_"
CACHE_ROOT_PARTS = (".splent_cache", "spls")
METADATA_FILENAME = "metadata.toml"
DEFAULT_MIRROR = "uvlhub.io"


# ---------------------------------------------------------------------------
# The pin: what a product records so a name can be resolved without a catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SplPin:
    """Everything needed to fetch one SPL model, plus what it belongs to.

    ``doi`` is the version DOI and is what gets downloaded. ``concept_doi``
    identifies the line across versions and is what tells a developer they
    have fallen behind. ``version`` is human-facing and is what the cache
    directory is keyed on when it is known.
    """

    name: str
    doi: str | None = None
    concept_doi: str | None = None
    version: str | None = None
    mirror: str = DEFAULT_MIRROR
    file: str | None = None
    description: str = ""

    @property
    def remote_file(self) -> str:
        """Filename to ask UVLHub for.

        The name on UVLHub is not always ``<name>.uvl`` (sample_splent_spl
        publishes ``sample_splent_app.uvl``), so it is recorded separately and
        only defaulted when nothing said otherwise.
        """
        return self.file or f"{self.name}.uvl"

    @property
    def fetchable(self) -> bool:
        return bool(self.doi and self.mirror)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def working_copy_dirname(name: str) -> str:
    """Directory name for the editable copy of *name*.

    SPL names already carry a ``_spl`` suffix by convention, and
    ``splent_spl_cms_spl`` reads like a stutter, so the suffix is dropped:
    ``cms_spl`` is edited in ``splent_spl_cms/``. The literal form is still
    accepted when resolving, so a repository cloned under the un-stripped name
    is never orphaned.
    """
    core = name[: -len("_spl")] if name.endswith("_spl") else name
    return f"{WORKING_COPY_PREFIX}{core or name}"


def working_copy_candidates(workspace: str | os.PathLike, name: str) -> list[Path]:
    """Every directory that may hold the editable copy, best first."""
    root = Path(workspace)
    stripped = working_copy_dirname(name)
    literal = f"{WORKING_COPY_PREFIX}{name}"
    seen: list[Path] = [root / stripped]
    if literal != stripped:
        seen.append(root / literal)
    return seen


def working_copy(workspace: str | os.PathLike, name: str) -> Path | None:
    """The editable copy of *name* if one exists in the workspace."""
    for candidate in working_copy_candidates(workspace, name):
        if candidate.is_dir():
            return candidate
    return None


def cache_root(workspace: str | os.PathLike) -> Path:
    return Path(workspace).joinpath(*CACHE_ROOT_PARTS)


def doi_slug(doi: str) -> str:
    """A DOI as a single filesystem-safe path segment.

    ``10.5281/zenodo.20837624`` becomes ``10.5281_zenodo.20837624``. Kept
    readable rather than hashed, because the point of the directory name is
    that a developer can look at the cache and see which record is in it.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "_", doi.strip()).strip("_")


def cache_dir(
    workspace: str | os.PathLike,
    name: str,
    version: str | None = None,
    doi: str | None = None,
) -> Path:
    """Cache directory for a model, keyed like a cached feature.

    ``<name>@<version>`` when a version is pinned, so two products pinning two
    versions never fight over one directory.

    ``<name>@<doi slug>`` when there is no version but there is a DOI. Both
    writers of pins (``spl:migrate-catalog`` and ``product:create``) record an
    empty version, so in practice almost every pin lands here. Keying those on
    the name alone made every product in a workspace share one directory: the
    first one to resolve won, and every other product silently derived from a
    model it does not pin. The DOI is what the pin actually promises, so the
    DOI is what the directory is named after.

    Plain ``<name>`` only when neither is known.
    """
    if version:
        leaf = f"{name}@{version}"
    elif doi:
        leaf = f"{name}@{doi_slug(doi)}"
    else:
        leaf = name
    return cache_root(workspace) / leaf


def cached_doi(directory: str | os.PathLike) -> str | None:
    """The DOI recorded in a cache directory's ``metadata.toml``, if any."""
    meta = Path(directory) / METADATA_FILENAME
    if not meta.is_file():
        return None
    data = _load_toml(meta)
    spl = data.get("spl", {}) if isinstance(data.get("spl"), dict) else {}
    uvl = spl.get("uvl", {}) if isinstance(spl.get("uvl"), dict) else {}
    recorded = str(uvl.get("doi") or "").strip()
    return recorded or None


def cache_candidates(
    workspace: str | os.PathLike,
    name: str,
    version: str | None = None,
    doi: str | None = None,
) -> list[Path]:
    """Cache directories to look in, most specific first.

    When *doi* is known, a directory whose recorded DOI contradicts it is not
    a candidate. A directory that records nothing is still accepted: it was
    written before DOIs were tracked, and refusing it would strand caches that
    are probably fine. A directory that names a different DOI is refused,
    because using it would derive a product from a model it does not pin.
    """
    dirs = [cache_dir(workspace, name, version)] if version else []
    if doi:
        dirs.append(cache_dir(workspace, name, None, doi))
    dirs.append(cache_dir(workspace, name, None))
    if not version:
        # A pin without a version can still be served by whatever versioned
        # copy is already on disk, which is what keeps `spl:fetch` then
        # offline work honest.
        root = cache_root(workspace)
        if root.is_dir():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and entry.name.startswith(f"{name}@"):
                    dirs.append(entry)

    seen: list[Path] = []
    for candidate in dirs:
        if candidate in seen:
            continue
        if doi:
            recorded = cached_doi(candidate)
            if recorded and recorded != doi:
                continue
        seen.append(candidate)
    return seen


def uvl_filename(name: str) -> str:
    """The model's filename on disk, whatever it is called on UVLHub."""
    return f"{name}.uvl"


# ---------------------------------------------------------------------------
# Reading pins
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def pin_from_metadata(name: str, data: dict) -> SplPin:
    """Build a pin from a ``metadata.toml`` body."""
    spl = data.get("spl", {}) if isinstance(data.get("spl"), dict) else {}
    uvl = spl.get("uvl", {}) if isinstance(spl.get("uvl"), dict) else {}
    return SplPin(
        name=str(spl.get("name") or name),
        doi=(str(uvl.get("doi")).strip() or None) if uvl.get("doi") else None,
        concept_doi=(
            (str(uvl.get("concept_doi")).strip() or None)
            if uvl.get("concept_doi")
            else None
        ),
        version=(str(uvl.get("version")).strip() or None)
        if uvl.get("version")
        else None,
        mirror=str(uvl.get("mirror") or DEFAULT_MIRROR),
        file=(str(uvl.get("file")).strip() or None) if uvl.get("file") else None,
        description=str(spl.get("description") or ""),
    )


def pin_from_pyproject(data: dict) -> SplPin | None:
    """Build a pin from a product's parsed ``pyproject.toml``.

    Returns None when the product declares no SPL at all. A product that
    declares a name but no DOI still gets a pin, because a workspace that has
    the working copy on disk does not need one.
    """
    splent = data.get("tool", {}).get("splent", {})
    name = splent.get("spl")
    if not name or not isinstance(name, str):
        return None
    model = splent.get("spl_model", {})
    if not isinstance(model, dict):
        model = {}

    def _val(key: str) -> str | None:
        raw = model.get(key)
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None

    return SplPin(
        name=name,
        doi=_val("doi"),
        concept_doi=_val("concept_doi"),
        version=_val("version"),
        mirror=_val("mirror") or DEFAULT_MIRROR,
        file=_val("file"),
    )


def read_pin(
    workspace: str | os.PathLike, name: str, *, product: str | None = None
) -> SplPin:
    """Best pin available for *name*, merging every place that knows something.

    Order, most authoritative first:

    1. the product asking, when one is asking, because its pin is the record
       that travels with the deployment;
    2. the working copy, because an author editing the model knows more about
       it than anything downstream does;
    3. any product in the workspace that pins this SPL. The commands that most
       need a DOI (spl:fetch, spl:list, spl:info) all run detached, so without
       this they would have nowhere to read one from and the catalog would
       still be load-bearing;
    4. the cache, which is whatever a previous fetch happened to record and is
       therefore the most likely to be stale.
    """
    sources: list[SplPin] = []

    if product:
        product_pin = read_product_pin(workspace, product)
        if product_pin and product_pin.name == name:
            sources.append(product_pin)

    copy = working_copy(workspace, name)
    if copy is not None:
        sources.append(pin_from_metadata(name, _load_toml(copy / METADATA_FILENAME)))

    for entry in _safe_iterdir(Path(workspace)):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if product and entry.name == product:
            continue
        candidate_pin = read_product_pin(workspace, entry.name)
        if candidate_pin and candidate_pin.name == name and candidate_pin.doi:
            sources.append(candidate_pin)

    for candidate in cache_candidates(workspace, name):
        meta = candidate / METADATA_FILENAME
        if meta.is_file():
            sources.append(pin_from_metadata(name, _load_toml(meta)))
            break

    merged = SplPin(name=name)
    for source in sources:
        merged = SplPin(
            name=name,
            doi=merged.doi or source.doi,
            concept_doi=merged.concept_doi or source.concept_doi,
            version=merged.version or source.version,
            mirror=merged.mirror if merged.mirror != DEFAULT_MIRROR else source.mirror,
            file=merged.file or source.file,
            description=merged.description or source.description,
        )
    return merged


def read_product_pin(workspace: str | os.PathLike, product: str) -> SplPin | None:
    """The pin recorded by *product*'s own pyproject.toml, if any."""
    path = Path(workspace) / product / "pyproject.toml"
    if not path.is_file():
        return None
    return pin_from_pyproject(_load_toml(path))


def active_product_pin(workspace: str | os.PathLike) -> SplPin | None:
    """The pin of the selected product, when one is selected."""
    product = os.getenv("SPLENT_APP")
    if not product:
        return None
    return read_product_pin(workspace, product)


# ---------------------------------------------------------------------------
# Writing pins into a product
# ---------------------------------------------------------------------------

_MODEL_HEADER = "[tool.splent.spl_model]"


def render_pin_block(pin: SplPin) -> str:
    """The ``[tool.splent.spl_model]`` block as it is written to disk."""
    lines = [
        "# Where the SPL model itself comes from. The name above is not enough",
        "# to download anything, so the DOI travels with the product and any",
        "# clone of this repository can resolve the model from UVLHub alone.",
        _MODEL_HEADER,
        f'mirror = "{pin.mirror or DEFAULT_MIRROR}"',
        f'doi = "{pin.doi or ""}"',
        "# Same line, every version. Used to report that a newer model exists.",
        f'concept_doi = "{pin.concept_doi or ""}"',
        f'version = "{pin.version or ""}"',
    ]
    if pin.file:
        lines.append(f'file = "{pin.file}"')
    return "\n".join(lines)


def _block_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Span of an existing spl_model block, comments above it included."""
    for index, line in enumerate(lines):
        if line.strip() != _MODEL_HEADER:
            continue
        start = index
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        end = index + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                break
            end += 1
        # Comments that introduce the NEXT table belong to it, not to this one.
        while end - 1 > index and lines[end - 1].lstrip().startswith("#"):
            end -= 1
        while end - 1 > index and not lines[end - 1].strip():
            end -= 1
        return start, end
    return None


def write_product_pin(pyproject_path: str | os.PathLike, pin: SplPin) -> None:
    """Record *pin* in a product's pyproject.toml, replacing any older block.

    The block is machine-managed, so it is replaced wholesale when present and
    appended at the end of the file when not. It is never inserted into the
    middle of ``[tool.splent]``: opening a sub-table there would silently
    reparent every key that follows it.
    """
    path = Path(pyproject_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    block = render_pin_block(pin).splitlines()

    bounds = _block_bounds(lines)
    if bounds is not None:
        start, end = bounds
        lines[start:end] = block
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(block)

    atomic_write(str(path), "\n".join(lines) + "\n")

    written = pin_from_pyproject(_load_toml(path))
    if written is None or written.doi != pin.doi:
        raise click.ClickException(
            f"{path} did not validate after recording the SPL model pin"
        )


def write_metadata(directory: str | os.PathLike, pin: SplPin) -> Path:
    """Write a ``metadata.toml`` describing *pin* into *directory*."""
    target = Path(directory) / METADATA_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    body = [
        "[spl]",
        f'name = "{pin.name}"',
        f'description = "{_toml_escape(pin.description)}"',
        "",
        "[spl.uvl]",
        f'mirror = "{pin.mirror or DEFAULT_MIRROR}"',
        f'doi = "{pin.doi or ""}"',
        f'concept_doi = "{pin.concept_doi or ""}"',
        f'version = "{pin.version or ""}"',
        f'file = "{pin.remote_file}"',
    ]
    atomic_write(str(target), "\n".join(body) + "\n")
    return target


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def find_uvl(
    workspace: str | os.PathLike,
    name: str,
    *,
    version: str | None = None,
    doi: str | None = None,
) -> str | None:
    """Path to the model if it is already on disk. Never touches the network.

    Pass *doi* whenever the caller has a pin. Without it the cache answers with
    whatever copy of *name* it happens to hold, which is how a product ended up
    deriving from another product's version of the same SPL.
    """
    copy = working_copy(workspace, name)
    if copy is not None:
        local = copy / uvl_filename(name)
        if local.is_file():
            return str(local)
        # An author may keep the model under the name UVLHub knows it by.
        for candidate in sorted(copy.glob("*.uvl")):
            return str(candidate)

    for directory in cache_candidates(workspace, name, version, doi):
        cached = directory / uvl_filename(name)
        if cached.is_file():
            return str(cached)
    return None


def fetch_uvl(
    workspace: str | os.PathLike,
    pin: SplPin,
    *,
    force: bool = False,
    quiet: bool = False,
) -> str:
    """Download the model named by *pin* into the cache and return its path.

    The cache is the only thing written. A working copy belongs to the
    developer and is never overwritten by a fetch.
    """
    from splent_cli.commands.uvl.uvl_utils import resolve_uvlhub_raw_url

    if not pin.fetchable:
        raise click.ClickException(
            f"No DOI recorded for '{pin.name}', so there is nothing to download.\n"
            f"  Either create the model locally (splent spl:create {pin.name}), or\n"
            f"  record its DOI in the product's pyproject.toml under "
            f"[tool.splent.spl_model]."
        )

    target_dir = cache_dir(workspace, pin.name, pin.version, pin.doi)
    target = target_dir / uvl_filename(pin.name)
    if target.is_file() and not force:
        return str(target)

    url = resolve_uvlhub_raw_url(pin.mirror, pin.doi, pin.remote_file)
    if not quiet:
        click.echo(f"  Downloading UVL from {url}")

    import requests

    try:
        response = requests.get(url, timeout=20)
    except requests.RequestException as exc:
        raise click.ClickException(f"Failed to download UVL: {exc}")

    if response.status_code != 200:
        raise click.ClickException(f"UVLHub returned {response.status_code} for {url}")

    target_dir.mkdir(parents=True, exist_ok=True)
    atomic_write(str(target), response.text)
    write_metadata(target_dir, pin)

    if not quiet:
        click.echo(f"  UVL cached at {target}")
    return str(target)


def resolve_uvl(
    workspace: str | os.PathLike,
    name: str,
    *,
    product: str | None = None,
    pin: SplPin | None = None,
    allow_fetch: bool = True,
    quiet: bool = False,
) -> str:
    """Turn an SPL name into a readable UVL path.

    Working copy, then cache, then UVLHub by DOI. Raises ClickException with
    the three places it looked when it comes up empty.
    """
    resolved_pin = pin or read_pin(workspace, name, product=product)

    local = find_uvl(
        workspace, name, version=resolved_pin.version, doi=resolved_pin.doi
    )
    if local:
        return local

    if allow_fetch and resolved_pin.fetchable:
        if not quiet:
            click.secho(
                f"  No local model for '{name}', fetching it from UVLHub...",
                fg="yellow",
            )
        return fetch_uvl(workspace, resolved_pin, quiet=quiet)

    raise click.ClickException(missing_model_message(workspace, name, resolved_pin))


def product_uvl(
    workspace: str | os.PathLike,
    product: str,
    *,
    allow_fetch: bool = False,
    quiet: bool = True,
) -> str | None:
    """The UVL a product derives from, or None when it has none on disk.

    The default is offline. Most callers are diagnostics or run inside a
    container during boot, where a silent network round trip is the wrong
    thing to do; the ones that genuinely need the file ask for ``allow_fetch``.
    """
    pin = read_product_pin(workspace, product)
    if pin is None:
        return None
    local = find_uvl(workspace, pin.name, version=pin.version, doi=pin.doi)
    if local or not allow_fetch or not pin.fetchable:
        return local
    try:
        return fetch_uvl(workspace, pin, quiet=quiet)
    except click.ClickException:
        return None


CATALOG_DIRNAME = "splent_catalog"
MIGRATE_HINT = "Run: splent spl:migrate-catalog"


def catalog_entry(workspace: str | os.PathLike, name: str) -> Path | None:
    """The leftover ``splent_catalog`` entry for *name*, if one is still there."""
    candidate = Path(workspace) / CATALOG_DIRNAME / name
    return candidate if candidate.is_dir() else None


def catalog_migration_pending(workspace: str | os.PathLike) -> list[str]:
    """SPL names whose only remaining description is the legacy catalog.

    Until ``spl:migrate-catalog`` has run, no product carries a
    ``[tool.splent.spl_model]`` block and nothing in the workspace can resolve
    a model. That is the default state of every workspace that predates the
    pin, so the commands a developer reaches for first have to name the fix
    instead of only reporting the symptom.
    """
    root = Path(workspace) / CATALOG_DIRNAME
    if not root.is_dir():
        return []
    pending = []
    for entry in sorted(_safe_iterdir(root)):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        name = entry.name
        if not _NAME_RE.match(name):
            continue
        if read_pin(workspace, name).fetchable:
            continue
        pending.append(name)
    return pending


def missing_model_message(
    workspace: str | os.PathLike, name: str, pin: SplPin | None = None
) -> str:
    """What to tell someone whose model is nowhere to be found."""
    pin = pin or SplPin(name=name)
    looked = [
        str(p / uvl_filename(name)) for p in working_copy_candidates(workspace, name)
    ]
    looked.extend(
        str(d / uvl_filename(name))
        for d in cache_candidates(workspace, name, pin.version, pin.doi)
    )
    lines = [f"No UVL model found for '{name}'.", "  Looked in:"]
    lines.extend(f"    {path}" for path in looked)
    if pin.fetchable:
        lines.append(f"  Download it with: splent spl:fetch {name}")
    else:
        lines.append(
            "  Nothing records a DOI for it. Record one under [tool.splent.spl_model]"
        )
        lines.append(
            f"  in the product's pyproject.toml, or run: splent spl:create {name}"
        )
    if catalog_entry(workspace, name) is not None:
        lines.append(f"  A splent_catalog entry still exists for it. {MIGRATE_HINT}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")


def known_spls(workspace: str | os.PathLike) -> list[str]:
    """Every SPL this workspace can name, from all three homes plus products.

    Working copies and cache entries are self-describing. Products contribute
    the SPLs they pin, which is what makes a workspace holding only products
    and no models still able to list, fetch and validate them.
    """
    root = Path(workspace)
    names: set[str] = set()

    for entry in _safe_iterdir(root):
        if not entry.is_dir() or not entry.name.startswith(WORKING_COPY_PREFIX):
            continue
        meta = pin_from_metadata("", _load_toml(entry / METADATA_FILENAME))
        if meta.name:
            names.add(meta.name)
            continue
        core = entry.name[len(WORKING_COPY_PREFIX) :]
        if core:
            names.add(core if core.endswith("_spl") else f"{core}_spl")

    for entry in _safe_iterdir(cache_root(root)):
        if entry.is_dir():
            names.add(entry.name.split("@", 1)[0])

    for entry in _safe_iterdir(root):
        if not entry.is_dir() or not (entry / "pyproject.toml").is_file():
            continue
        pin = pin_from_pyproject(_load_toml(entry / "pyproject.toml"))
        if pin and _NAME_RE.match(pin.name):
            names.add(pin.name)

    return sorted(names)


def products_pinning(workspace: str | os.PathLike, name: str) -> list[tuple[str, str]]:
    """(product, pinned version) for every product in the workspace using *name*.

    Publishing a new version does not move any product onto it. Saying which
    products are still on the old one is the difference between a DOI that was
    minted and a DOI that is in use.
    """
    found: list[tuple[str, str]] = []
    for entry in _safe_iterdir(Path(workspace)):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        pin = read_product_pin(workspace, entry.name)
        if pin and pin.name == name:
            found.append((entry.name, pin.version or pin.doi or "unpinned"))
    return found


def _safe_iterdir(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except (OSError, FileNotFoundError):
        return []
