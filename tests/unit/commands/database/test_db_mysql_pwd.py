"""Tests that db:dump and db:restore use MYSQL_PWD env var, not -p flag."""
import subprocess
from unittest.mock import patch, MagicMock, call
from click.testing import CliRunner
from splent_cli.commands.database.db_dump import db_dump
from splent_cli.commands.database.db_restore import db_restore


class TestDbDumpMysqlPwd:
    def test_password_not_in_command_args(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "secret123")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            m = MagicMock()
            m.returncode = 0
            return m

        runner = CliRunner(mix_stderr=False)
        dump_file = str(tmp_path / "dump.sql")
        with patch("splent_cli.commands.database.db_dump.subprocess.run", side_effect=fake_run):
            runner.invoke(db_dump, [dump_file])

        assert not any("secret123" in str(arg) for arg in captured.get("cmd", []))
        assert captured.get("env", {}).get("MYSQL_PWD") == "secret123"

    def test_password_not_in_command_args_restore(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "secret456")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")

        dump_file = tmp_path / "dump.sql"
        dump_file.write_text("-- sql dump")

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            m = MagicMock()
            m.returncode = 0
            return m

        runner = CliRunner(mix_stderr=False)
        with patch("splent_cli.commands.database.db_restore.subprocess.run", side_effect=fake_run):
            runner.invoke(db_restore, [str(dump_file), "--yes"])

        assert not any("secret456" in str(arg) for arg in captured.get("cmd", []))
        assert captured.get("env", {}).get("MYSQL_PWD") == "secret456"
