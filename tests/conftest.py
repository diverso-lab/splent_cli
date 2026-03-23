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
        '[project.optional-dependencies]\nfeatures = []\n'
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


def make_cache_entry(workspace_path: Path, namespace: str, name: str, version: str | None = None) -> Path:
    """
    Create a feature cache directory entry.

    version=None  → editable: .splent_cache/features/{namespace}/{name}/
    version="v1"  → versioned: .splent_cache/features/{namespace}/{name}@v1/
    """
    dir_name = f"{name}@{version}" if version else name
    path = workspace_path / ".splent_cache" / "features" / namespace / dir_name
    path.mkdir(parents=True, exist_ok=True)
    return path
