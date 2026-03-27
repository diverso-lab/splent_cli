"""Tests for pure helper functions in release_core.py."""
import pytest
from unittest.mock import patch, MagicMock

from splent_cli.commands.release.release_core import (
    extract_repo,
    update_version,
    validate_env,
)

_RUN = "splent_cli.commands.release.release_core.subprocess.run"


# ---------------------------------------------------------------------------
# validate_env
# ---------------------------------------------------------------------------

class TestValidateEnv:
    def test_passes_when_all_vars_set(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        monkeypatch.delenv("PYPI_USERNAME", raising=False)
        monkeypatch.delenv("PYPI_PASSWORD", raising=False)
        validate_env()  # must not raise

    def test_exits_when_twine_username_missing(self, monkeypatch):
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.delenv("PYPI_USERNAME", raising=False)
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        with pytest.raises(SystemExit) as exc:
            validate_env()
        assert exc.value.code == 1

    def test_exits_when_twine_password_missing(self, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.delenv("PYPI_PASSWORD", raising=False)
        with pytest.raises(SystemExit) as exc:
            validate_env()
        assert exc.value.code == 1

    def test_pypi_username_accepted_as_fallback(self, monkeypatch):
        monkeypatch.delenv("TWINE_USERNAME", raising=False)
        monkeypatch.setenv("PYPI_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        validate_env()  # must not raise

    def test_pypi_password_accepted_as_fallback(self, monkeypatch):
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.delenv("TWINE_PASSWORD", raising=False)
        monkeypatch.setenv("PYPI_PASSWORD", "pypi-abc")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc")
        validate_env()  # must not raise

    def test_no_github_token_does_not_exit(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("TWINE_USERNAME", "__token__")
        monkeypatch.setenv("TWINE_PASSWORD", "pypi-abc")
        validate_env()  # must not raise — token missing is just a warning


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
        # The top-level version line is updated
        assert 'version = "1.5.0"' in content


# ---------------------------------------------------------------------------
# extract_repo
# ---------------------------------------------------------------------------

class TestExtractRepo:
    def _mock_run(self, url: str):
        mock = MagicMock()
        mock.stdout = url + "\n"
        return mock

    def test_https_url_with_token(self, tmp_path):
        with patch(_RUN, return_value=self._mock_run(
            "https://ghp_token@github.com/myorg/myrepo.git"
        )):
            result = extract_repo(str(tmp_path))
        assert result == "myorg/myrepo"

    def test_https_url_without_token(self, tmp_path):
        with patch(_RUN, return_value=self._mock_run(
            "https://github.com/myorg/myrepo.git"
        )):
            result = extract_repo(str(tmp_path))
        assert result == "myorg/myrepo"

    def test_ssh_url(self, tmp_path):
        with patch(_RUN, return_value=self._mock_run(
            "git@github.com:myorg/myrepo.git"
        )):
            result = extract_repo(str(tmp_path))
        assert result == "myorg/myrepo"

    def test_url_without_git_suffix(self, tmp_path):
        with patch(_RUN, return_value=self._mock_run(
            "https://github.com/myorg/myrepo"
        )):
            result = extract_repo(str(tmp_path))
        assert result == "myorg/myrepo"

    def test_unknown_url_raises_system_exit(self, tmp_path):
        with patch(_RUN, return_value=self._mock_run("not-a-valid-git-url")):
            with pytest.raises(SystemExit):
                extract_repo(str(tmp_path))
