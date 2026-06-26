"""Tests for feature:clone — path traversal validation and error handling."""

from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.feature.feature_clone import (
    feature_clone,
    _validate_identifier_part,
)
from splent_cli.utils.git_url import https_url, ssh_url, candidate_urls


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
    def test_https_display_url_has_no_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "secret_token_abc")
        real_url, display_url = https_url("myorg", "myrepo")
        assert "secret_token_abc" not in display_url
        assert "github.com/myorg/myrepo" in display_url
        assert "secret_token_abc" in real_url

    def test_ssh_url_format(self):
        assert ssh_url("myorg", "myrepo") == "git@github.com:myorg/myrepo.git"

    def test_candidate_urls_ssh_first_https_second(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "secret_token_abc")
        cands = candidate_urls("myorg", "myrepo")
        assert [c[2] for c in cands] == ["ssh", "https"]
        # SSH candidate: real == display, no token.
        assert cands[0][0] == "git@github.com:myorg/myrepo.git"
        assert cands[0][0] == cands[0][1]
        # HTTPS candidate: token only in the real URL, never in display.
        assert "secret_token_abc" in cands[1][0]
        assert "secret_token_abc" not in cands[1][1]


class TestRepoNotFound:
    def test_shows_clean_error_when_repo_not_found(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        runner = CliRunner(mix_stderr=False)

        # The clone now runs through git_url.clone, which shells out via the
        # proc.run wrapper (imported into git_url as `run`). Patch that and
        # require_tool so a host without git doesn't short-circuit first.
        with (
            patch("splent_cli.commands.feature.feature_clone.require_tool"),
            patch("splent_cli.utils.git_url.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=128,
                stderr="fatal: repository not found",
                stdout="",
            )
            result = runner.invoke(
                feature_clone, ["splent-io/nonexistent@v1.0.0"]
            )

        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "❌" in result.output
        # Crucially: no traceback
        assert "Traceback" not in result.output
        assert "CalledProcessError" not in result.output
