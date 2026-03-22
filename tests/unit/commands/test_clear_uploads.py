"""
Tests for the clear:uploads command.
"""
import pytest
from unittest.mock import patch
from click.testing import CliRunner

from splent_cli.commands.clear_uploads import clear_uploads


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


class TestClearUploads:
    def test_clears_files_in_uploads_dir(self, runner, tmp_path):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        (uploads / "file1.txt").write_text("data")
        (uploads / "file2.txt").write_text("more data")

        with patch("splent_cli.commands.clear_uploads.PathUtils.get_uploads_dir", return_value=str(uploads)):
            result = runner.invoke(clear_uploads, [])

        assert result.exit_code == 0
        assert "successfully cleared" in result.output
        assert list(uploads.iterdir()) == []

    def test_clears_subdirs_in_uploads(self, runner, tmp_path):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        subdir = uploads / "user123"
        subdir.mkdir()
        (subdir / "photo.jpg").write_bytes(b"\xff")

        with patch("splent_cli.commands.clear_uploads.PathUtils.get_uploads_dir", return_value=str(uploads)):
            result = runner.invoke(clear_uploads, [])

        assert result.exit_code == 0
        assert not subdir.exists()

    def test_keeps_uploads_dir_itself(self, runner, tmp_path):
        uploads = tmp_path / "uploads"
        uploads.mkdir()

        with patch("splent_cli.commands.clear_uploads.PathUtils.get_uploads_dir", return_value=str(uploads)):
            result = runner.invoke(clear_uploads, [])

        assert result.exit_code == 0
        assert uploads.exists()

    def test_shows_warning_when_dir_missing(self, runner, tmp_path):
        missing = tmp_path / "uploads"

        with patch("splent_cli.commands.clear_uploads.PathUtils.get_uploads_dir", return_value=str(missing)):
            result = runner.invoke(clear_uploads, [])

        assert result.exit_code == 0
        assert "does not exist" in result.output

    def test_shows_error_on_permission_failure(self, runner, tmp_path):
        uploads = tmp_path / "uploads"
        uploads.mkdir()
        (uploads / "file.txt").write_text("x")

        def boom(path):
            raise PermissionError("denied")

        with patch("splent_cli.commands.clear_uploads.PathUtils.get_uploads_dir", return_value=str(uploads)):
            with patch("os.remove", side_effect=boom):
                result = runner.invoke(clear_uploads, [])

        assert result.exit_code == 0
        assert "Error" in result.output
