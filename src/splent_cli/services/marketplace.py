"""Marketplace index: build, save, load and query.

The index is a REGENERABLE CACHE derived from the feature contracts
(``[tool.splent.contract]`` in each feature's pyproject.toml at its latest
released tag) plus the SPL catalog. It is never a second source of truth:
rebuilding it from the sources must always be safe, and nothing in the
index can be edited without the change originating in a feature repo.

Layout of the index document (schema 1)::

    {
      "schema": 1,
      "generated_at": "...", "generator": "splent_cli/x.y.z",
      "sources": {"orgs": [...], "repos": [...], "workspace": bool},
      "features": [ {feature entry}, ... ],
      "spls": [ {spl entry}, ... ],
      "collisions": [ {"kind": "route", "item": "/x", "features": [...]}, ... ]
    }

Feature entries carry the full contract (provides / requires / extensible /
docker), the derived archetype, presentation metadata (description,
category, tags), distribution state (latest tag, PyPI presence) and the
computed reverse dependencies (``used_by``).
"""

import json
import os
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

INDEX_SCHEMA = 1
FEATURE_PREFIX = "splent_feature_"

# Feeds feature:search / feature:info when no explicit path is given.
INDEX_URL_ENV = "SPLENT_INDEX_URL"


# ── Paths ─────────────────────────────────────────────────────────────


def index_cache_path(workspace: str) -> Path:
    return Path(workspace) / ".splent_cache" / "marketplace" / "index.json"


# ── Feature entries ───────────────────────────────────────────────────


def feature_entry_from_pyproject(
    pyproject_text: str,
    *,
    org: str,
    repo: str,
    source: str,
    version: str | None,
    github: dict | None = None,
) -> dict:
    """Build one index entry from a feature's pyproject.toml contents.

    ``source`` is ``"github"`` (contract read at a released tag) or
    ``"workspace"`` (editable feature scanned locally).
    """
    data = tomllib.loads(pyproject_text)
    project = data.get("project", {})
    splent = data.get("tool", {}).get("splent", {})
    contract = splent.get("contract", {})

    short = repo.removeprefix(FEATURE_PREFIX)
    description = contract.get("description") or project.get("description") or ""

    entry = {
        "id": f"{org}/{repo}",
        "org": org,
        "repo": repo,
        "short": short,
        "version": version,
        "project_version": project.get("version"),
        "description": description,
        "archetype": contract.get("archetype"),
        "category": contract.get("category"),
        "tags": contract.get("tags", []),
        "env": contract.get("env"),
        "provides": contract.get("provides", {}),
        "requires": contract.get("requires", {}),
        "extensible": contract.get("extensible", {}),
        "docker": contract.get("docker") or None,
        "refinement": splent.get("refinement") or None,
        "cli_version": splent.get("cli_version"),
        "requires_python": project.get("requires-python"),
        "dependencies": project.get("dependencies", []),
        "source": source,
        "used_by": [],
        "pypi": None,
        "github": github,
    }
    return entry


def build_remote_features(
    orgs: list[str],
    extra_repos: list[str],
    *,
    token: str | None,
    check_pypi: bool = True,
    progress=None,
) -> tuple[list[dict], list[str]]:
    """Index every ``splent_feature_*`` repo reachable from the sources.

    ``extra_repos`` are explicit ``org/repo`` references (third-party features
    living outside the indexed orgs). Returns ``(entries, problems)`` where
    problems are human-readable strings for repos that could not be indexed —
    the index build NEVER fails silently.
    """
    from splent_cli.services import registry

    def _note(msg: str):
        if progress:
            progress(msg)

    targets: list[tuple[str, dict | None]] = []
    problems: list[str] = []

    for org in orgs:
        repos = registry.list_org_repos(org, token)
        if repos is None:
            problems.append(f"org '{org}' not found or not accessible")
            continue
        for r in repos:
            name = r.get("name", "")
            if name.startswith(FEATURE_PREFIX) and not r.get("archived"):
                targets.append((f"{org}/{name}", r))

    for ref in extra_repos:
        targets.append((ref, None))

    entries: list[dict] = []
    seen: set[str] = set()
    for ref, repo_meta in targets:
        if ref in seen:
            continue
        seen.add(ref)
        org, _, repo = ref.partition("/")
        _note(f"indexing {ref}")

        if repo_meta is None:
            repo_meta = registry.fetch_repo(org, repo, token)
            if repo_meta is None:
                problems.append(f"{ref}: repo not found")
                continue

        # Resolve the version loudly: a rate limit here must abort the build
        # (propagating RegistryError) instead of masquerading as "no released
        # tags" and silently producing a truncated index.
        tags = registry.list_tags(org, repo, token, max_pages=1)
        ordered = registry.semver_sorted(tags)
        version = ordered[0] if ordered else (tags[0] if tags else None)
        if not version:
            problems.append(f"{ref}: no released tags — skipped (release it first)")
            continue

        pyproject_text = registry.fetch_file(
            org, repo, "pyproject.toml", ref=version, token=token
        )
        if pyproject_text is None:
            problems.append(f"{ref}@{version}: pyproject.toml not found at tag")
            continue

        try:
            entry = feature_entry_from_pyproject(
                pyproject_text,
                org=org,
                repo=repo,
                source="github",
                version=version,
                github={
                    "url": repo_meta.get("html_url"),
                    "description": repo_meta.get("description"),
                    "pushed_at": repo_meta.get("pushed_at"),
                    "stars": repo_meta.get("stargazers_count", 0),
                    "private": repo_meta.get("private", False),
                },
            )
        except tomllib.TOMLDecodeError as e:
            problems.append(f"{ref}@{version}: invalid pyproject.toml ({e})")
            continue

        if check_pypi:
            pypi = registry.pypi_versions(repo)
            entry["pypi"] = {
                "published": bool(pypi),
                "latest": pypi[0] if pypi else None,
                "has_current": version.lstrip("v") in pypi if pypi else False,
            }

        entries.append(entry)

    return entries, problems


def build_workspace_features(workspace: str) -> list[dict]:
    """Index the editable features present at the workspace root (no network)."""
    entries = []
    for pyproject in sorted(Path(workspace).glob(f"{FEATURE_PREFIX}*/pyproject.toml")):
        repo = pyproject.parent.name
        try:
            text = pyproject.read_text()
            entry = feature_entry_from_pyproject(
                text,
                org=os.getenv("SPLENT_DEFAULT_NAMESPACE", "splent_io").replace(
                    "_", "-"
                ),
                repo=repo,
                source="workspace",
                version=None,
            )
        except (OSError, tomllib.TOMLDecodeError):
            continue
        entries.append(entry)
    return entries


# ── Computed relations ────────────────────────────────────────────────


def compute_used_by(entries: list[dict]) -> None:
    """Fill ``used_by`` on every entry from the others' ``requires.features``."""
    by_short: dict[str, dict] = {e["short"]: e for e in entries}
    for e in entries:
        e["used_by"] = []
    for e in entries:
        for dep in e.get("requires", {}).get("features", []):
            target = by_short.get(dep)
            if target and e["short"] not in target["used_by"]:
                target["used_by"].append(e["short"])
    for e in entries:
        e["used_by"].sort()


def compute_collisions(entries: list[dict]) -> list[dict]:
    """Features providing the same route/service/model — they cannot coexist
    in one product unless an SPL alternative group makes them exclusive."""
    collisions = []
    for kind in ("routes", "services", "models"):
        providers: dict[str, list[str]] = {}
        for e in entries:
            for item in e.get("provides", {}).get(kind, []):
                providers.setdefault(item, []).append(e["short"])
        for item, feats in sorted(providers.items()):
            if len(set(feats)) > 1:
                collisions.append(
                    {"kind": kind[:-1], "item": item, "features": sorted(set(feats))}
                )
    return collisions


# ── SPL catalog ───────────────────────────────────────────────────────


def parse_uvl_structure(uvl_text: str) -> dict:
    """Best-effort structural parse of a UVL model.

    Returns ``{"features": {short: {...}}, "alternative_groups": [...],
    "constraints": [[a, b], ...]}``. Leaf features carry their ``org`` /
    ``package`` attributes plus whether they sit under ``mandatory`` or
    ``optional`` and, when applicable, the alternative group that owns them.
    """
    features: dict[str, dict] = {}
    alt_groups: list[dict] = []
    constraints: list[list[str]] = []

    in_features = False
    in_constraints = False
    # Stack of (indent, kind, name) where kind is "feature" or a group keyword.
    stack: list[tuple[int, str, str | None]] = []

    for raw_line in uvl_text.splitlines():
        if not raw_line.strip():
            continue
        stripped = raw_line.strip()
        indent = len(raw_line) - len(raw_line.lstrip("\t "))

        if stripped == "features":
            in_features, in_constraints = True, False
            stack = []
            continue
        if stripped == "constraints":
            in_features, in_constraints = False, True
            continue

        if in_constraints:
            if "=>" in stripped:
                left, _, right = stripped.partition("=>")
                a = left.split("#")[0].strip()
                b = right.split("#")[0].strip()
                if a and b:
                    constraints.append([a, b])
            continue

        if not in_features:
            continue

        while stack and stack[-1][0] >= indent:
            stack.pop()

        if stripped in ("mandatory", "optional", "alternative", "or"):
            stack.append((indent, stripped, None))
            continue

        m = re.match(r"(\w+)\s*(?:\{([^}]*)\})?", stripped)
        if not m:
            continue
        name, attrs = m.group(1), m.group(2) or ""

        group_kinds = [kind for _, kind, _ in stack if kind != "feature"]
        parent_features = [n for _, kind, n in stack if kind == "feature" and n]
        nearest_group = group_kinds[-1] if group_kinds else None

        pkg_m = re.search(r"package\s+'([^']+)'", attrs)
        org_m = re.search(r"org\s+'([^']+)'", attrs)

        if pkg_m:
            features[name] = {
                "org": org_m.group(1) if org_m else None,
                "package": pkg_m.group(1),
                "presence": "mandatory" if "mandatory" in group_kinds else "optional",
                "group": parent_features[-1] if nearest_group in ("alternative", "or") else None,
                "group_kind": nearest_group if nearest_group in ("alternative", "or") else None,
            }
            if nearest_group in ("alternative", "or") and parent_features:
                owner = parent_features[-1]
                group = next(
                    (g for g in alt_groups if g["owner"] == owner), None
                )
                if group is None:
                    group = {
                        "owner": owner,
                        "kind": nearest_group,
                        "members": [],
                    }
                    alt_groups.append(group)
                group["members"].append(name)

        stack.append((indent, "feature", name))

    return {
        "features": features,
        "alternative_groups": alt_groups,
        "constraints": constraints,
    }


def build_spls(workspace: str) -> list[dict]:
    """Index every SPL in splent_catalog: metadata + parsed UVL structure."""
    catalog = Path(workspace) / "splent_catalog"
    spls = []
    if not catalog.is_dir():
        return spls
    for metadata_path in sorted(catalog.glob("*/metadata.toml")):
        spl_dir = metadata_path.parent
        try:
            metadata = tomllib.loads(metadata_path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        spl_meta = metadata.get("spl", {})
        entry = {
            "name": spl_dir.name,
            "description": spl_meta.get("description", ""),
            "uvl": spl_meta.get("uvl", {}),
            "model": None,
        }
        uvl_path = spl_dir / f"{spl_dir.name}.uvl"
        if uvl_path.is_file():
            try:
                entry["model"] = parse_uvl_structure(uvl_path.read_text())
            except OSError:
                pass
        spls.append(entry)
    return spls


# ── Assemble / persist / load ─────────────────────────────────────────


def assemble_index(
    features: list[dict], spls: list[dict], sources: dict
) -> dict:
    compute_used_by(features)
    features = sorted(features, key=lambda e: e["short"])
    try:
        import importlib.metadata

        cli_version = importlib.metadata.version("splent_cli")
    except Exception:
        cli_version = "unknown"
    return {
        "schema": INDEX_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": f"splent_cli/{cli_version}",
        "sources": sources,
        "features": features,
        "spls": spls,
        "collisions": compute_collisions(features),
    }


def save_index(index: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")


def load_index_file(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA:
        return None
    return data


def fetch_remote_index(url: str) -> dict | None:
    """Download an index.json published elsewhere (GitHub Pages, raw file…)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "splent-cli"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != INDEX_SCHEMA:
        return None
    return data


def resolve_index(workspace: str, *, refresh: bool = False) -> tuple[dict | None, str]:
    """Best index available without building: remote URL or local cache.

    Returns ``(index, origin)`` where origin describes where it came from
    (``"url"``, ``"cache"`` or ``""`` when none is available). With
    ``refresh=True`` the remote URL (if configured) is always re-fetched.
    """
    cache = index_cache_path(workspace)
    url = os.getenv(INDEX_URL_ENV)

    if url and (refresh or not cache.is_file()):
        index = fetch_remote_index(url)
        if index is not None:
            save_index(index, cache)
            return index, "url"

    index = load_index_file(cache)
    if index is not None:
        return index, "cache"

    if url:
        index = fetch_remote_index(url)
        if index is not None:
            save_index(index, cache)
            return index, "url"

    return None, ""


# ── Query ─────────────────────────────────────────────────────────────


def find_feature(index: dict, ref: str) -> dict | None:
    """Locate a feature entry by ``short``, ``repo`` name or ``org/repo``."""
    ref = ref.strip()
    ref_short = ref.split("/")[-1].split("@")[0].removeprefix(FEATURE_PREFIX)
    has_org = "/" in ref
    org = ref.split("/", 1)[0] if has_org else None
    for e in index.get("features", []):
        if e["short"] == ref_short and (not has_org or e["org"] == org):
            return e
    return None


def search_features(
    index: dict,
    query: str | None = None,
    *,
    category: str | None = None,
    archetype: str | None = None,
    tag: str | None = None,
    provides: str | None = None,
    requires: str | None = None,
) -> list[dict]:
    """Filter index entries. All criteria are ANDed; query is a substring
    match over name, description, tags and provided services/models."""
    results = []
    q = query.lower() if query else None
    for e in index.get("features", []):
        if category and (e.get("category") or "").lower() != category.lower():
            continue
        if archetype and (e.get("archetype") or "").lower() != archetype.lower():
            continue
        if tag and tag.lower() not in [t.lower() for t in e.get("tags", [])]:
            continue
        if requires and requires.lower() not in [
            r.lower() for r in e.get("requires", {}).get("features", [])
        ]:
            continue
        if provides:
            haystack = []
            for kind in ("routes", "services", "models", "hooks", "signals"):
                haystack.extend(e.get("provides", {}).get(kind, []))
            if provides.lower() not in [h.lower() for h in haystack]:
                continue
        if q:
            haystack_parts = [
                e["short"],
                e["repo"],
                e.get("description") or "",
                " ".join(e.get("tags", [])),
                " ".join(e.get("provides", {}).get("services", [])),
                " ".join(e.get("provides", {}).get("models", [])),
            ]
            if q not in " ".join(haystack_parts).lower():
                continue
        results.append(e)
    return results


def dependency_closure(index: dict, short: str) -> list[str]:
    """Transitive requires.features closure for a feature (excluding itself)."""
    by_short = {e["short"]: e for e in index.get("features", [])}
    seen: set[str] = set()
    queue = [short]
    while queue:
        current = queue.pop()
        entry = by_short.get(current)
        if not entry:
            continue
        for dep in entry.get("requires", {}).get("features", []):
            if dep not in seen:
                seen.add(dep)
                queue.append(dep)
    seen.discard(short)
    return sorted(seen)
