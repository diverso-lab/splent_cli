"""Tests that db:dump and db:restore use MYSQL_PWD env var, not -p flag."""
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.database.db_dump import db_dump
from splent_cli.commands.database.db_restore import db_restore


class TestDbDumpMysqlPwd:
    def test_password_not_in_command_args(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "secret123")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env", {})
            out = kwargs.get("stdout")
            if out and hasattr(out, "write"):
                out.write(b"-- dump")
            m = MagicMock()
            m.returncode = 0
            return m

        runner = CliRunner(mix_stderr=False)
        dump_file = str(tmp_path / "dump.sql")
        # Patch subprocess at the real boundary (proc.run wraps subprocess.run).
        with patch("splent_cli.utils.proc.subprocess.run", side_effect=fake_run):
            runner.invoke(db_dump, [dump_file])

        assert not any("secret123" in str(arg) for arg in captured.get("cmd", []))
        assert captured.get("env", {}).get("MYSQL_PWD") == "secret123"

    def test_password_not_in_command_args_restore(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPLENT_APP", "test_app")
        monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "secret456")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")

        dump_file = tmp_path / "dump.sql"
        dump_file.write_text("-- sql dump")

        captured = {"cmds": []}

        def fake_run(cmd, **kwargs):
            # db:restore now calls mysqldump (pre-restore backup) then mysql;
            # record every invocation so we check ALL of them for the password.
            captured["cmds"].append(cmd)
            captured["env"] = kwargs.get("env", {})
            out = kwargs.get("stdout")
            if out and hasattr(out, "write"):
                out.write(b"-- backup")
            m = MagicMock()
            m.returncode = 0
            return m

        runner = CliRunner(mix_stderr=False)
        # Patch subprocess at the real boundary (proc.run wraps subprocess.run).
        with patch("splent_cli.utils.proc.subprocess.run", side_effect=fake_run):
            runner.invoke(db_restore, [str(dump_file), "--yes"])

        all_args = [arg for cmd in captured["cmds"] for arg in cmd]
        assert not any("secret456" in str(arg) for arg in all_args)
        assert captured.get("env", {}).get("MYSQL_PWD") == "secret456"
