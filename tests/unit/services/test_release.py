"""Tests for the release service — error handling in git operations."""
import subprocess
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
import pytest
from splent_cli.services.release import (
    commit_local_changes,
    create_and_push_git_tag,
    build_and_upload_pypi,
    extract_repo,
)


class TestExtractRepo:
    def test_https_with_token(self):
        assert (
            extract_repo("https://token@github.com/org/repo.git")
            == "org/repo"
        )

    def test_https_plain(self):
        assert (
            extract_repo("https://github.com/org/repo.git") == "org/repo"
        )

    def test_ssh(self):
        assert (
            extract_repo("git@github.com:org/repo.git") == "org/repo"
        )

    def test_invalid_raises_system_exit(self):
        with pytest.raises(SystemExit):
            extract_repo("not-a-valid-url")


class TestCommitLocalChanges:
    def test_clean_tree_skips_commit(self, tmp_path):
        with patch(
            "splent_cli.services.release.subprocess.run"
        ) as mock_run:
            mock_result = MagicMock()
            mock_result.stdout = ""
            mock_run.return_value = mock_result

            import click

            @click.command()
            def cmd():
                commit_local_changes(str(tmp_path), "v1.0.0")

            result = CliRunner().invoke(cmd)
            assert "clean" in result.output.lower()
            assert mock_run.call_count == 1  # only git status

    def test_git_push_failure_exits_with_message(self, tmp_path):
        with patch(
            "splent_cli.services.release.subprocess.run"
        ) as mock_run:
            with patch(
                "splent_cli.services.release.click.confirm",
                return_value=True,
            ):
                dirty_result = MagicMock()
                dirty_result.stdout = "M pyproject.toml"
                # status → dirty, add → ok, commit → ok, push → fail
                error = subprocess.CalledProcessError(1, "git push")
                error.stderr = "Permission denied"
                mock_run.side_effect = [
                    dirty_result, None, None, error
                ]

                import click

                @click.command()
                def cmd():
                    commit_local_changes(str(tmp_path), "v1.0.0")

                result = CliRunner(mix_stderr=False).invoke(cmd)
                assert result.exit_code == 1
                assert (
                    "failed" in result.output.lower()
                    or "❌" in result.output
                )
                assert "Traceback" not in result.output

    def test_git_push_failure_explains_partial_state(self, tmp_path):
        with patch(
            "splent_cli.services.release.subprocess.run"
        ) as mock_run:
            with patch(
                "splent_cli.services.release.click.confirm",
                return_value=True,
            ):
                dirty_result = MagicMock()
                dirty_result.stdout = "M pyproject.toml"
                error = subprocess.CalledProcessError(1, "git push")
                error.stderr = "network error"
                mock_run.side_effect = [
                    dirty_result, None, None, error
                ]

                import click

                @click.command()
                def cmd():
                    commit_local_changes(str(tmp_path), "v1.0.0")

                result = CliRunner(mix_stderr=False).invoke(cmd)
                # Should mention that version was bumped but not committed
                assert (
                    "pyproject" in result.output.lower()
                    or "version" in result.output.lower()
                    or "committed" in result.output.lower()
                )


class TestCreateAndPushGitTag:
    def test_tag_push_failure_exits_cleanly(self, tmp_path):
        with patch(
            "splent_cli.services.release.subprocess.run"
        ) as mock_run:
            # fetch → ok, tag list → ok (no tags), create tag → ok,
            # push tag → fail
            tags_result = MagicMock()
            tags_result.stdout = ""
            error = subprocess.CalledProcessError(1, "git push")
            error.stderr = "rejected"
            mock_run.side_effect = [
                MagicMock(), tags_result, MagicMock(), error
            ]

            import click

            @click.command()
            def cmd():
                create_and_push_git_tag(str(tmp_path), "v1.0.0")

            result = CliRunner(mix_stderr=False).invoke(cmd)
            assert result.exit_code == 1
            assert "Traceback" not in result.output
            assert "❌" in result.output


class TestBuildAndUploadPypi:
    def test_build_failure_exits_cleanly(self, tmp_path):
        with patch(
            "splent_cli.services.release.subprocess.run"
        ) as mock_run:
            error = subprocess.CalledProcessError(1, "python -m build")
            mock_run.side_effect = [
                MagicMock(), error
            ]  # rm -rf ok, build fails

            import click

            @click.command()
            def cmd():
                build_and_upload_pypi(str(tmp_path))

            result = CliRunner(mix_stderr=False).invoke(cmd)
            assert result.exit_code == 1
            assert "Traceback" not in result.output
            assert (
                "build failed" in result.output.lower()
                or "❌" in result.output
            )

    def test_upload_failure_shows_manual_recovery_hint(self, tmp_path):
        with patch(
            "splent_cli.services.release.subprocess.run"
        ) as mock_run:
            error = subprocess.CalledProcessError(1, "twine upload")
            mock_run.side_effect = [
                MagicMock(), MagicMock(), error
            ]  # rm, build ok, upload fails

            import click

            @click.command()
            def cmd():
                build_and_upload_pypi(str(tmp_path))

            result = CliRunner(mix_stderr=False).invoke(cmd)
            assert result.exit_code == 1
            assert "twine upload" in result.output
            assert "Traceback" not in result.output
