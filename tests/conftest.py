"""
Shared pytest fixtures for the SPLENT CLI test suite.

How this works
--------------
- `runner`           → a Click test runner (captures stdout/stderr, no real TTY needed)
- `workspace`        → a real temporary directory acting as /workspace
- `product_workspace`→ workspace + test_app product fully set up
- `env_file`         → helper to write a .env into a workspace

The key trick: set WORKING_DIR to str(tmp_path) and the context.workspace() service
picks it up automatically. No Docker, no real filesystem, no env vars leak between tests.
"""

import pytest
from click.testing import CliRunner
from pathlib import Path


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner():
    """A Click CliRunner that mixes stdout/stderr into a single stream."""
    return CliRunner(mix_stderr=False)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """
    A temporary directory wired up as the SPLENT workspace root.
    Sets WORKING_DIR so context.workspace() returns this path.
    """
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.delenv("SPLENT_APP", raising=False)
    monkeypatch.delenv("SPLENT_ENV", raising=False)
    return tmp_path


@pytest.fixture
def product_workspace(tmp_path, monkeypatch):
    """
    A temporary workspace with a minimal 'test_app' product wired up.

    Layout:
        tmp_path/
          .env
          test_app/
            pyproject.toml
            docker/
              docker-compose.dev.yml
              docker-compose.prod.yml
    """
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")
    monkeypatch.setenv("SPLENT_ENV", "dev")

    product_dir = tmp_path / "test_app"
    docker_dir = product_dir / "docker"
    docker_dir.mkdir(parents=True)

    (product_dir / "pyproject.toml").write_text(
        '[project]\nname = "test_app"\nversion = "1.0.0"\n'
        "[project.optional-dependencies]\nfeatures = []\n"
    )
    (docker_dir / "docker-compose.dev.yml").write_text("services: {}")
    (docker_dir / "docker-compose.prod.yml").write_text("services: {}")

    (tmp_path / ".env").write_text("SPLENT_APP=test_app\nSPLENT_ENV=dev\n")

    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_env_file(workspace_path: Path, content: str) -> Path:
    """Write a .env file into workspace_path and return its path."""
    env_file = workspace_path / ".env"
    env_file.write_text(content)
    return env_file


def make_cache_entry(
    workspace_path: Path, namespace: str, name: str, version: str | None = None
) -> Path:
    """
    Create a feature cache directory entry.

    version=None  → editable: .splent_cache/features/{namespace}/{name}/
    version="v1"  → versioned: .splent_cache/features/{namespace}/{name}@v1/
    """
    dir_name = f"{name}@{version}" if version else name
    path = workspace_path / ".splent_cache" / "features" / namespace / dir_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _metadata_toml(
    name: str,
    *,
    doi: str = "",
    concept_doi: str = "",
    version: str = "",
    file: str | None = None,
    description: str = "",
) -> str:
    return (
        "[spl]\n"
        f'name = "{name}"\n'
        f'description = "{description}"\n'
        "\n"
        "[spl.uvl]\n"
        'mirror = "uvlhub.io"\n'
        f'doi = "{doi}"\n'
        f'concept_doi = "{concept_doi}"\n'
        f'version = "{version}"\n'
        f'file = "{file or name + ".uvl"}"\n'
    )


def make_spl_working_copy(
    workspace_path: Path,
    name: str,
    uvl_text: str,
    *,
    doi: str = "",
    concept_doi: str = "",
    version: str = "",
    file: str | None = None,
    description: str = "",
) -> Path:
    """The editable copy of an SPL model: workspace/splent_spl_<name>/.

    This is where a model being authored lives, and it is the first place the
    resolver looks. Mirrors ``splent_feature_<name>/``.
    """
    core = name[: -len("_spl")] if name.endswith("_spl") else name
    path = workspace_path / f"splent_spl_{core}"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.uvl").write_text(uvl_text, encoding="utf-8")
    (path / "metadata.toml").write_text(
        _metadata_toml(
            name,
            doi=doi,
            concept_doi=concept_doi,
            version=version,
            file=file,
            description=description,
        ),
        encoding="utf-8",
    )
    return path


def make_spl_cache_entry(
    workspace_path: Path,
    name: str,
    uvl_text: str,
    *,
    version: str | None = None,
    doi: str = "",
    concept_doi: str = "",
) -> Path:
    """A consumed SPL model: workspace/.splent_cache/spls/<name>[@<version>]/.

    Derived data. Deleting it must never lose anything the product pin cannot
    recreate.
    """
    leaf = f"{name}@{version}" if version else name
    path = workspace_path / ".splent_cache" / "spls" / leaf
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{name}.uvl").write_text(uvl_text, encoding="utf-8")
    (path / "metadata.toml").write_text(
        _metadata_toml(name, doi=doi, concept_doi=concept_doi, version=version or ""),
        encoding="utf-8",
    )
    return path


def write_product_spl_pin(
    workspace_path: Path,
    product: str,
    name: str,
    *,
    doi: str = "",
    concept_doi: str = "",
    version: str = "",
) -> Path:
    """Append a ``[tool.splent.spl_model]`` block to a product's pyproject."""
    path = workspace_path / product / "pyproject.toml"
    block = (
        "\n[tool.splent.spl_model]\n"
        'mirror = "uvlhub.io"\n'
        f'doi = "{doi}"\n'
        f'concept_doi = "{concept_doi}"\n'
        f'version = "{version}"\n'
    )
    path.write_text(path.read_text(encoding="utf-8") + block, encoding="utf-8")
    return path
