"""Tests for db:dump — env var validation before running mysqldump."""
from click.testing import CliRunner
from splent_cli.commands.database.db_dump import db_dump


class TestDbDumpEnvValidation:
    def test_exits_when_hostname_missing(self, monkeypatch):
        monkeypatch.delenv("MARIADB_HOSTNAME", raising=False)
        monkeypatch.setenv("MARIADB_USER", "root")
        monkeypatch.setenv("MARIADB_PASSWORD", "pass")
        monkeypatch.setenv("MARIADB_DATABASE", "mydb")
        runner = CliRunner(mix_stderr=True)
        result = runner.invoke(db_dump, [])
        assert result.exit_code == 1
        assert "MARIADB_HOSTNAME" in result.output

    def test_exits_when_all_missing(self, monkeypatch):
        for var in ("MARIADB_HOSTNAME", "MARIADB_USER", "MARIADB_PASSWORD", "MARIADB_DATABASE"):
            monkeypatch.delenv(var, raising=False)
        runner = CliRunner(mix_stderr=True)
        result = runner.invoke(db_dump, [])
        assert result.exit_code == 1
        assert "Missing" in result.output
