"""Tests for db:dump — temp-file+os.replace safety and credential handling.

db:dump shells out via splent_cli.utils.proc.run, which calls subprocess.run.
We patch subprocess at that real boundary (splent_cli.utils.proc.subprocess)
so these tests exercise the actual command/env that would reach the OS.
"""

import gzip
import os
import stat
import subprocess
import time
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from splent_cli.commands.database.db_dump import db_dump, MIN_DUMP_BYTES

# Realistic payload: comfortably above the truncation threshold so the
# success paths are not rejected by the minimum-size check.
DUMP_PAYLOAD = b"-- dump contents\n".ljust(MIN_DUMP_BYTES * 2, b"x")


def _set_db_env(monkeypatch):
    monkeypatch.setenv("SPLENT_APP", "test_app")
    monkeypatch.setenv("MARIADB_HOSTNAME", "localhost")
    monkeypatch.setenv("MARIADB_USER", "root")
    monkeypatch.setenv("MARIADB_PASSWORD", "pass")
    monkeypatch.setenv("MARIADB_DATABASE", "mydb")


def _write_payload(payload):
    def side_effect(*args, **kwargs):
        out = kwargs.get("stdout")
        if out and hasattr(out, "write"):
            out.write(payload)
        return MagicMock(returncode=0)

    return side_effect


class TestDbDumpCleanup:
    def test_removes_partial_file_on_failure(self, tmp_path, monkeypatch):
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                # Simulate mysqldump partially writing the temp file, then
                # failing. The final target file must NOT be created, and no
                # stray temp file may be left behind.
                def side_effect(*args, **kwargs):
                    fname = kwargs.get("stdout")
                    if fname and hasattr(fname, "write"):
                        fname.write(b"partial data")
                    raise subprocess.CalledProcessError(1, "mysqldump")

                mock_run.side_effect = side_effect

                result = runner.invoke(db_dump, ["test_dump.sql"])

                # mysqldump failed: the command surfaces an error (non-zero).
                assert result.exit_code != 0
                # Safety: no partial FINAL file is left behind.
                assert not os.path.exists("test_dump.sql")
                # Safety: no stray temp file is left behind either.
                leftovers = [f for f in os.listdir(".") if f.startswith(".db_dump_")]
                assert leftovers == []

    def test_success_creates_file(self, tmp_path, monkeypatch):
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(DUMP_PAYLOAD)
                result = runner.invoke(db_dump, ["test_dump.sql"])

                assert result.exit_code == 0
                assert "successfully" in result.output.lower()
                # Safety: the FINAL file exists after success...
                assert os.path.exists("test_dump.sql")
                # ...with the dumped contents (proves os.replace promoted the
                # temp file), and no temp file is left behind.
                with open("test_dump.sql", "rb") as fh:
                    assert fh.read() == DUMP_PAYLOAD
                leftovers = [f for f in os.listdir(".") if f.startswith(".db_dump_")]
                assert leftovers == []

    def test_rejects_implausibly_small_dump(self, tmp_path, monkeypatch):
        """A dump below MIN_DUMP_BYTES is truncated output, never promoted."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(b"-- tiny")

                result = runner.invoke(db_dump, ["test_dump.sql"])

                assert result.exit_code != 0
                assert not os.path.exists("test_dump.sql")
                leftovers = [f for f in os.listdir(".") if f.startswith(".db_dump_")]
                assert leftovers == []

    def test_gzip_flag_compresses_dump(self, tmp_path, monkeypatch):
        """--gzip produces a .sql.gz file whose decompressed content is the dump."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(DUMP_PAYLOAD)

                result = runner.invoke(db_dump, ["test_dump.sql", "--gzip"])

                assert result.exit_code == 0
                assert os.path.exists("test_dump.sql.gz")
                assert not os.path.exists("test_dump.sql")
                with gzip.open("test_dump.sql.gz", "rb") as fh:
                    assert fh.read() == DUMP_PAYLOAD
                leftovers = [f for f in os.listdir(".") if f.startswith(".db_dump_")]
                assert leftovers == []

    def test_gzip_dump_is_not_world_readable(self, tmp_path, monkeypatch):
        """A dump holds every password hash, so it must land 0600 like the
        uncompressed path does, not 0644 through the umask."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(DUMP_PAYLOAD)

                result = runner.invoke(db_dump, ["test_dump.sql", "--gzip"])

                assert result.exit_code == 0
                mode = stat.S_IMODE(os.stat("test_dump.sql.gz").st_mode)
                assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0
                # The plain path already had this property; keep them aligned.
                result = runner.invoke(db_dump, ["plain_dump.sql"])
                assert result.exit_code == 0
                plain_mode = stat.S_IMODE(os.stat("plain_dump.sql").st_mode)
                assert plain_mode & (stat.S_IRGRP | stat.S_IROTH) == 0

    def test_mysqldump_reliability_flags(self, tmp_path, monkeypatch):
        """The consistency/streaming/charset flags are always passed."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        captured_args = []
        with runner.isolated_filesystem():
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:

                def capture(*args, **kwargs):
                    if args:
                        captured_args.extend(args[0])
                    return _write_payload(DUMP_PAYLOAD)(*args, **kwargs)

                mock_run.side_effect = capture
                result = runner.invoke(db_dump, ["test_dump.sql"])

                assert result.exit_code == 0
                assert "--single-transaction" in captured_args
                assert "--quick" in captured_args
                assert "--default-character-set=utf8mb4" in captured_args


class TestDbDumpRetention:
    def test_retention_never_prunes_other_databases(self, tmp_path, monkeypatch):
        """Dumps land in a directory shared by every product, so retention
        must only ever consider dumps of the database being dumped."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            # Dumps of a DIFFERENT database, plus this database's own history
            # and a pre-restore backup that must never be treated as a dump.
            other_names = [
                "dump_otherdb_20240101_000000.sql",
                "dump_otherdb_20240102_000000.sql.gz",
                "dump_otherdb_20240103_000000.sql",
            ]
            own_names = [
                "dump_mydb_20240101_000000.sql",
                "dump_mydb_20240102_000000.sql",
            ]
            backup = "pre_restore_mydb_20240101_000000.sql"
            now = time.time()
            for i, name in enumerate(other_names + own_names + [backup]):
                with open(name, "wb") as fh:
                    fh.write(b"old")
                os.utime(name, (now - 100 + i, now - 100 + i))

            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(DUMP_PAYLOAD)

                # Retention 1: only the dump just created may survive among
                # this database's dumps.
                result = runner.invoke(db_dump, ["--retention", "1"])

                assert result.exit_code == 0
                remaining = set(os.listdir("."))
                # Every other database's dumps are untouched.
                for name in other_names:
                    assert name in remaining
                # The pre-restore backup of the same database is untouched.
                assert backup in remaining
                # This database's older dumps are pruned...
                for name in own_names:
                    assert name not in remaining
                # ...leaving exactly the new one.
                fresh = [
                    f
                    for f in remaining
                    if f.startswith("dump_mydb_") and not f.startswith("dump_mydb_2024")
                ]
                assert len(fresh) == 1

    def test_default_filename_includes_database(self, tmp_path, monkeypatch):
        """The generated name carries the database so prefixes never collide."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(DUMP_PAYLOAD)

                result = runner.invoke(db_dump, [])

                assert result.exit_code == 0
                created = [f for f in os.listdir(".") if f.endswith(".sql")]
                assert len(created) == 1
                assert created[0].startswith("dump_mydb_")

    def test_retention_prunes_old_timestamped_dumps(self, tmp_path, monkeypatch):
        """--retention N keeps the N newest dumps with the same prefix and
        never touches files that do not match the timestamped dump shape."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            # Pre-existing dumps, oldest first (mtime staggered explicitly so
            # the ordering does not depend on filesystem timestamp precision).
            old_names = [
                "dump_mydb_20240101_000000.sql",
                "dump_mydb_20240102_000000.sql.gz",
                "dump_mydb_20240103_000000.sql",
            ]
            now = time.time()
            for i, name in enumerate(old_names):
                with open(name, "wb") as fh:
                    fh.write(b"old")
                os.utime(name, (now - 100 + i, now - 100 + i))
            # Unrelated files that must survive pruning.
            with open("dump_mydb_notes.txt", "wb") as fh:
                fh.write(b"keep me")
            with open("other_20240101_000000.sql", "wb") as fh:
                fh.write(b"different prefix")

            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(DUMP_PAYLOAD)

                result = runner.invoke(db_dump, ["--retention", "2"])

                assert result.exit_code == 0
                remaining = sorted(os.listdir("."))
                # The new dump plus the newest old one survive (N=2)...
                new_dumps = [
                    f
                    for f in remaining
                    if f.startswith("dump_mydb_")
                    and f.endswith(".sql")
                    and not f.startswith("dump_mydb_2024")
                ]
                assert len(new_dumps) == 1
                assert "dump_mydb_20240103_000000.sql" in remaining
                # ...the two oldest are pruned...
                assert "dump_mydb_20240101_000000.sql" not in remaining
                assert "dump_mydb_20240102_000000.sql.gz" not in remaining
                # ...and unrelated files are untouched.
                assert "dump_mydb_notes.txt" in remaining
                assert "other_20240101_000000.sql" in remaining

    def test_no_retention_keeps_everything(self, tmp_path, monkeypatch):
        """Without --retention, pre-existing dumps are never deleted."""
        _set_db_env(monkeypatch)

        runner = CliRunner(mix_stderr=False)
        with runner.isolated_filesystem():
            with open("dump_mydb_20240101_000000.sql", "wb") as fh:
                fh.write(b"old")

            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:
                mock_run.side_effect = _write_payload(DUMP_PAYLOAD)

                result = runner.invoke(db_dump, [])

                assert result.exit_code == 0
                assert os.path.exists("dump_mydb_20240101_000000.sql")


class TestDbDumpCredentialsNotInArgs:
    def test_password_not_in_process_args_list(self, tmp_path, monkeypatch):
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
            with patch("splent_cli.utils.proc.subprocess.run") as mock_run:

                def capture(*args, **kwargs):
                    if args:
                        captured_args.extend(args[0])
                    captured_env.update(kwargs.get("env") or {})
                    out = kwargs.get("stdout")
                    if out and hasattr(out, "write"):
                        out.write(DUMP_PAYLOAD)
                    return MagicMock(returncode=0)

                mock_run.side_effect = capture
                runner.invoke(db_dump, ["test.sql"])

        # Security guarantee: the password is passed via MYSQL_PWD, never argv.
        password_in_args = any("supersecret" in str(a) for a in captured_args)
        assert password_in_args is False
        assert captured_env.get("MYSQL_PWD") == "supersecret"
