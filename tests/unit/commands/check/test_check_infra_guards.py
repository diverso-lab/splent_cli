"""
Tests for check:infra — diagnostic safety guards plus core happy paths.

check:infra is a *diagnostic*: it must never crash with a traceback, no matter
what the docker boundary or the on-disk config throws at it. All subprocess.run
calls are mocked — no real Docker / daemon / network required.

check_infra does ``import subprocess`` directly, so the boundary to patch is
``splent_cli.commands.check.check_infra.subprocess.run``.
"""
import json
import subprocess

import pytest
from unittest.mock import patch
from click.testing import CliRunner

from splent_cli.commands.check.check_infra import (
    check_infra,
    _run,
    _parse_compose_ports,
    _parse_compose_services,
)


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _make_product(tmp_path, monkeypatch, *, features=None, with_compose=True):
    """Create a minimal workspace with a product pyproject (+ optional compose)
    and point the context env vars at it. Returns the product dir Path."""
    prod = tmp_path / "test_app"
    (prod / "docker").mkdir(parents=True)

    feats = features or []
    feats_toml = ", ".join(f'"{f}"' for f in feats)
    (prod / "pyproject.toml").write_text(
        f"[tool.splent]\nfeatures = [{feats_toml}]\n"
    )
    if with_compose:
        (prod / "docker" / "docker-compose.yml").write_text("services: {}\n")

    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")
    return prod


def _no_traceback(text):
    assert "Traceback" not in text
    assert "CalledProcessError" not in text
    assert "TimeoutExpired" not in text


# ---------------------------------------------------------------------------
# HARDENED: the diagnostic must never crash on a broken docker boundary
# ---------------------------------------------------------------------------

class TestDockerMissingNeverCrashes:
    def test_docker_binary_missing_does_not_traceback(
        self, tmp_path, monkeypatch, runner
    ):
        """docker not installed (FileNotFoundError) must be absorbed: the
        command completes cleanly with no traceback, never propagating the
        FileNotFoundError out of the diagnostic."""
        _make_product(tmp_path, monkeypatch)
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = runner.invoke(check_infra, [])

        # Did not blow up with an unhandled exception.
        assert not isinstance(result.exception, FileNotFoundError)
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        # It still produced its structured report rather than dying early.
        assert "Infrastructure check" in result.stdout

    def test_docker_daemon_error_does_not_traceback(
        self, tmp_path, monkeypatch, runner
    ):
        """Daemon down: docker exits non-zero with an error on stderr. The
        diagnostic treats it as 'nothing queryable' and never crashes."""
        _make_product(tmp_path, monkeypatch)

        def daemon_down(cmd, **kwargs):
            return subprocess.CompletedProcess(
                cmd,
                returncode=1,
                stdout="",
                stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
            )

        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=daemon_down,
        ):
            result = runner.invoke(check_infra, [])

        assert result.exception is None or isinstance(result.exception, SystemExit)
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        assert "Infrastructure check" in result.stdout

    def test_docker_timeout_does_not_traceback(
        self, tmp_path, monkeypatch, runner
    ):
        """A hung daemon (TimeoutExpired) is absorbed, not surfaced as a crash."""
        _make_product(tmp_path, monkeypatch)
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=subprocess.TimeoutExpired("docker", 30),
        ):
            result = runner.invoke(check_infra, [])

        assert not isinstance(result.exception, subprocess.TimeoutExpired)
        _no_traceback(result.output)
        _no_traceback(result.stderr)


# ---------------------------------------------------------------------------
# HARDENED: malformed / missing pyproject yields a clean FAIL naming the file
# ---------------------------------------------------------------------------

class TestPyprojectGuards:
    def test_malformed_pyproject_is_clean_fail_naming_it(
        self, tmp_path, monkeypatch, runner
    ):
        """Invalid TOML in pyproject must produce a clean error that names the
        file (not a raw TOMLDecodeError traceback) and a non-zero exit."""
        prod = _make_product(tmp_path, monkeypatch)
        (prod / "pyproject.toml").write_text("this is = = not valid toml [[[\n")

        # docker is irrelevant here; keep it from doing anything real.
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = runner.invoke(check_infra, [])

        assert result.exit_code != 0
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        # The clean ClickException prints to stderr (mix_stderr=False) and
        # must name the offending file.
        combined = result.stdout + result.stderr
        assert "pyproject.toml" in combined
        assert "TOML" in combined or "valid" in combined.lower()

    def test_missing_pyproject_is_clean_fail(
        self, tmp_path, monkeypatch, runner
    ):
        """No pyproject at all → clean FAIL message, non-zero exit, no crash."""
        prod = _make_product(tmp_path, monkeypatch)
        (prod / "pyproject.toml").unlink()

        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = runner.invoke(check_infra, [])

        assert result.exit_code != 0
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        assert "pyproject.toml not found" in result.stdout


# ---------------------------------------------------------------------------
# HARDENED: parsing helpers tolerate a broken docker boundary
# ---------------------------------------------------------------------------

class TestRunHelperGuards:
    def test_run_maps_missing_tool_to_nonzero(self):
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            res = _run(["docker", "ps"])
        assert res.returncode != 0
        assert "not found" in res.stderr

    def test_run_maps_timeout_to_nonzero(self):
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=subprocess.TimeoutExpired("docker", 30),
        ):
            res = _run(["docker", "ps"])
        assert res.returncode != 0
        assert "timed out" in res.stderr


class TestParseHelpersTolerateBadInput:
    def test_parse_ports_returns_empty_when_docker_fails(self):
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert _parse_compose_ports("any.yml") == []

    def test_parse_ports_returns_empty_on_malformed_json(self):
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="{ not valid json", stderr=""
            ),
        ):
            assert _parse_compose_ports("any.yml") == []

    def test_parse_services_returns_empty_on_malformed_json(self):
        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            return_value=subprocess.CompletedProcess(
                [], 0, stdout="<<<not json>>>", stderr=""
            ),
        ):
            assert _parse_compose_services("any.yml") == []


# ---------------------------------------------------------------------------
# Core happy paths
# ---------------------------------------------------------------------------

class TestHappyPaths:
    def test_clean_config_all_pass(self, tmp_path, monkeypatch, runner):
        """A single product compose with one unique published port and no
        conflicts passes with exit 0."""
        _make_product(tmp_path, monkeypatch)
        cfg = json.dumps(
            {
                "services": {
                    "web": {"ports": [{"published": "8000", "target": 80}]}
                }
            }
        )

        def fake_run(cmd, **kwargs):
            if "config" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=cfg, stderr="")
            # docker ps / network ls etc. -> empty success
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(check_infra, [])

        assert result.exit_code == 0
        _no_traceback(result.output)
        assert "checks passed" in result.stdout
        assert "[FAIL]" not in result.stdout

    def test_port_conflict_across_features_is_fail(
        self, tmp_path, monkeypatch, runner
    ):
        """Same published port declared by a feature and the product is a
        real conflict → [FAIL] naming the port and a non-zero exit."""
        _make_product(tmp_path, monkeypatch, features=["splent_io/feat_a"])
        # Editable feature at workspace root with its own compose file.
        feat_docker = tmp_path / "feat_a" / "docker"
        feat_docker.mkdir(parents=True)
        (feat_docker / "docker-compose.yml").write_text("services: {}\n")

        cfg = json.dumps(
            {
                "services": {
                    "web": {"ports": [{"published": "8080", "target": 80}]}
                }
            }
        )

        def fake_run(cmd, **kwargs):
            if "config" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=cfg, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch(
            "splent_cli.commands.check.check_infra.subprocess.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(check_infra, [])

        assert result.exit_code != 0
        _no_traceback(result.output)
        assert "[FAIL]" in result.stdout
        assert "8080" in result.stdout
        assert "feat_a/web" in result.stdout
        assert "test_app/web" in result.stdout
