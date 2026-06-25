"""Tests for db:dump — temp-file+os.replace safety and credential handling.

db:dump shells out via splent_cli.utils.proc.run, which calls subprocess.run.
We patch subprocess at that real boundary (splent_cli.utils.proc.subprocess)
so these tests exercise the actual command/env that would reach the OS.
"""
import os
import subprocess
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.database.db_dump import db_dump


class TestDbDumpCleanup:
    def test_removes_partial_file_on_failure(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "pass")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch(
                "splent_cli.utils.proc.subprocess.run"
            ) as mock_run:
                # Simulate mysqldump partially writing the temp file, then
                # failing. The final target file must NOT be created, and no
                # stray temp file may be left behind.
                def side_effect(*args, **kwargs):
                    fname = kwargs.get("stdout")
                    if fname and hasattr(fname, "write"):
                        fname.write(b"partial data")
                    raise subprocess.CalledProcessError(
                        1, "mysqldump"
                    )

                mock_run.side_effect = side_effect

                result = runner.invoke(db_dump, ["test_dump.sql"])

                # mysqldump failed: the command surfaces an error (non-zero).
                assert result.exit_code != 0
                # Safety: no partial FINAL file is left behind.
                assert not os.path.exists("test_dump.sql")
                # Safety: no stray temp file is left behind either.
                leftovers = [
                    f
                    for f in os.listdir(".")
                    if f.startswith(".db_dump_")
                ]
                assert leftovers == []

    def test_success_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "pass")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch(
                "splent_cli.utils.proc.subprocess.run"
            ) as mock_run:
                def side_effect(*args, **kwargs):
                    out = kwargs.get("stdout")
                    if out and hasattr(out, "write"):
                        out.write(b"-- dump contents")
                    return MagicMock(returncode=0)

                mock_run.side_effect = side_effect
                result = runner.invoke(db_dump, ["test_dump.sql"])

                assert result.exit_code == 0
                assert "successfully" in result.output.lower()
                # Safety: the FINAL file exists after success...
                assert os.path.exists("test_dump.sql")
                # ...with the dumped contents (proves os.replace promoted the
                # temp file), and no temp file is left behind.
                with open("test_dump.sql", "rb") as fh:
                    assert fh.read() == b"-- dump contents"
                leftovers = [
                    f
                    for f in os.listdir(".")
                    if f.startswith(".db_dump_")
                ]
                assert leftovers == []


class TestDbDumpCredentialsNotInArgs:
    def test_password_not_in_process_args_list(
        self, tmp_path, monkeypatch
    ):
        """Password must never appear in argv (would leak via ps)."""
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "supersecret")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")

        runner = CliRunner(mix_stderr=False)
        captured_args = []
        captured_env = {}

        with runner.isolated_filesystem():
            with patch(
                "splent_cli.utils.proc.subprocess.run"
            ) as mock_run:
                def capture(*args, **kwargs):
                    if args:
                        captured_args.extend(args[0])
                    captured_env.update(kwargs.get("env") or {})
                    out = kwargs.get("stdout")
                    if out and hasattr(out, "write"):
                        out.write(b"-- dump")
                    return MagicMock(returncode=0)

                mock_run.side_effect = capture
                runner.invoke(db_dump, ["test.sql"])

        # Security guarantee: the password is passed via MYSQL_PWD, never argv.
        password_in_args = any(
            "supersecret" in str(a) for a in captured_args
        )
        assert password_in_args is False
        assert captured_env.get("MYSQL_PWD") == "supersecret"
