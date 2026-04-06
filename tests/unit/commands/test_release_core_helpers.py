"""Tests for shared release helpers in services/release.py."""
import pytest
from unittest.mock import patch, MagicMock

from splent_cli.services.release import (
    extract_repo,
    update_version,
    validate_release_env,
)


# ---------------------------------------------------------------------------
# validate_release_env
# ---------------------------------------------------------------------------

class TestValidateReleaseEnv:
    def test_passes_when_all_vars_set(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        monkeypatch.delenv("PYPI_USERNAME", raising=False)
        monkeypatch.delenv("PYPI_PASSWORD", raising=False)
        validate_release_env()  # must not raise

    def test_exits_when_twine_username_missing(self, monkeypatch):
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.delenv("PYPI_USERNAME", raising=False)
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        with pytest.raises(SystemExit) as exc:
            validate_release_env()
        assert exc.value.code == 1

    def test_exits_when_twine_password_missing(self, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.delenv("PYPI_PASSWORD", raising=False)
        with pytest.raises(SystemExit) as exc:
            validate_release_env()
        assert exc.value.code == 1

    def test_pypi_username_accepted_as_fallback(self, monkeypatch):
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.setenv("PYPI_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        validate_release_env()  # must not raise

    def test_pypi_password_accepted_as_fallback(self, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.setenv("PYPI_PASSWORD", "pypi-abc")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        validate_release_env()  # must not raise

    def test_no_github_token_does_not_exit(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        validate_release_env()  # must not raise — token missing is just a warning

    def test_docker_validation_when_required(self, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        monkeypatch.delenv("DOCKERHUB_USERNAME", raising=False)
        with pytest.raises(SystemExit) as exc:
            validate_release_env(require_docker=True)
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# update_version
# ---------------------------------------------------------------------------

class TestUpdateVersion:
    def test_replaces_double_quoted_version(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text('[project]\nname = "mypkg"\nversion = "1.0.0"\n')
        update_version(str(p), "1.2.3")
        assert 'version = "1.2.3"' in p.read_text()

    def test_replaces_single_quoted_version(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text("[project]\nname = 'mypkg'\nversion = '0.9.0'\n")
        update_version(str(p), "2.0.0")
        assert 'version = "2.0.0"' in p.read_text()

    def test_preserves_rest_of_file(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        original = '[project]\nname = "mypkg"\nversion = "1.0.0"\n[tool.splent]\nfoo = "bar"\n'
        p.write_text(original)
        update_version(str(p), "1.1.0")
        content = p.read_text()
        assert '[tool.splent]' in content
        assert 'foo = "bar"' in content

    def test_updates_only_version_line(self, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(
            '[project]\nversion = "1.0.0"\n'
            '[tool]\nother_version = "not-this"\n'
        )
        update_version(str(p), "1.5.0")
        content = p.read_text()
        assert 'version = "1.5.0"' in content


# ---------------------------------------------------------------------------
# extract_repo
# ---------------------------------------------------------------------------

class TestExtractRepo:
    def test_https_url_with_token(self):
        result = extract_repo("https://ghp_token@github.com/myorg/myrepo.git")
        assert result == "myorg/myrepo"

    def test_https_url_without_token(self):
        result = extract_repo("https://github.com/myorg/myrepo.git")
        assert result == "myorg/myrepo"

    def test_ssh_url(self):
        result = extract_repo("git@github.com:myorg/myrepo.git")
        assert result == "myorg/myrepo"

    def test_url_without_git_suffix(self):
        result = extract_repo("https://github.com/myorg/myrepo")
        assert result == "myorg/myrepo"

    def test_unknown_url_raises_system_exit(self):
        with pytest.raises(SystemExit):
            extract_repo("not-a-valid-git-url")
