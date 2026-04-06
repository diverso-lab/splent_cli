"""Tests for feature:clone — path traversal validation and error handling."""
import subprocess
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.feature.feature_clone import (
    feature_clone,
    _validate_identifier_part,
    _build_repo_url,
)


class TestPathTraversalValidation:
    def test_rejects_path_traversal_in_namespace(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(feature_clone, ["../../evil/repo@v1.0.0"])
        assert result.exit_code == 1
        assert "Invalid" in result.output

    def test_rejects_slash_in_repo_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(feature_clone, ["org/re/po@v1.0.0"])
        # re/po as repo name after split — "re" is valid, "po@v1.0.0" is
        # valid, depends on split. Actually the split is on "/" so
        # "org/re/po@v1.0.0" → namespace="org", rest="re/po@v1.0.0"
        # → should fail
        assert result.exit_code == 1

    def test_accepts_valid_identifier(self):
        # Should not raise
        _validate_identifier_part("splent-io", "namespace")
        _validate_identifier_part("splent_feature_auth", "repo")
        _validate_identifier_part("my.org", "namespace")

    def test_rejects_special_chars(self):
        import pytest
        with pytest.raises(SystemExit):
            _validate_identifier_part("../../evil", "namespace")

    def test_rejects_empty_string(self):
        import pytest
        with pytest.raises(SystemExit):
            _validate_identifier_part("", "namespace")


class TestTokenNotExposedInURL:
    def test_display_url_has_no_token_https(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "secret_token_abc")
        with patch("splent_cli.utils.git_url._ssh_available", return_value=False):
            _, display_url = _build_repo_url("myorg", "myrepo")
        assert "secret_token_abc" not in display_url
        assert "github.com/myorg/myrepo" in display_url

    def test_real_url_contains_token_https(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "secret_token_abc")
        with patch("splent_cli.utils.git_url._ssh_available", return_value=False):
            real_url, _ = _build_repo_url("myorg", "myrepo")
        assert "secret_token_abc" in real_url

    def test_ssh_both_urls_equal(self, monkeypatch):
        with patch("splent_cli.utils.git_url._ssh_available", return_value=True):
            real_url, display_url = _build_repo_url("myorg", "myrepo")
        assert real_url == display_url
        assert "@github.com" in real_url


class TestRepoNotFound:
    def test_shows_clean_error_when_repo_not_found(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        runner = CliRunner(mix_stderr=False)

        with patch("splent_cli.utils.git_url._ssh_available", return_value=False), \
             patch(
                "splent_cli.commands.feature.feature_clone.subprocess.run"
             ) as mock_run, \
             patch(
                "splent_cli.commands.feature.feature_clone.requests.get"
             ) as mock_get:
            mock_run.side_effect = subprocess.CalledProcessError(
                128, "git clone"
            )
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = []
            mock_get.return_value = mock_resp

            result = runner.invoke(
                feature_clone, ["splent-io/nonexistent@v1.0.0"]
            )

        assert result.exit_code == 1
        assert (
            "not found" in result.output.lower()
            or "❌" in result.output
        )
        # Crucially: no traceback
        assert "Traceback" not in result.output
        assert "CalledProcessError" not in result.output
