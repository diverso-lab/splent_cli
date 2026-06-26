"""
Regression net for the "external tool missing / external tool failed" hardening
of the feature commands that shell out:

  * feature:compile  → bash / docker / npx  (via proc.run + subprocess.Popen)
  * feature:env      → cp / shutil.copy
  * feature:git      → git                  (via proc.run)

The contract being locked in: when the underlying tool is absent or returns a
non-zero exit, the user sees a *clean* actionable message — never a raw
FileNotFoundError / CalledProcessError traceback — and the exit code reflects
the failure. Normal arg passthrough for feature:git still forwards the git
exit code.

All boundaries are mocked: no real docker / git / npx / bash / network.
"""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from splent_cli.commands.feature.feature_compile import _compile_in_container
from splent_cli.commands.feature.feature_env import feature_env
from splent_cli.commands.feature.feature_git import feature_git


@pytest.fixture
def runner():
    return CliRunner(mix_stderr=False)


def _no_traceback(text: str) -> bool:
    """True when the surfaced text carries no leaked Python traceback noise."""
    return (
        "Traceback" not in text
        and "FileNotFoundError" not in text
        and "CalledProcessError" not in text
    )


# ---------------------------------------------------------------------------
# feature:compile — bash / docker / npx guards
#
# _compile_in_container() is the unit that actually shells out. The non-watch
# path goes through proc.run (→ subprocess.run); the watch path uses
# subprocess.Popen directly inside a try/except FileNotFoundError.
# ---------------------------------------------------------------------------


class TestCompileToolMissing:
    def _run(self, container_id, watch, missing):
        """Drive _compile_in_container with the shell-out boundary patched.

        ``missing`` raises FileNotFoundError from the boundary (tool absent).
        """
        with (
            patch(
                "splent_cli.commands.feature.feature_compile.os.path.exists",
                return_value=True,
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                side_effect=FileNotFoundError() if missing else None,
            ) as mock_run,
            patch(
                "splent_cli.commands.feature.feature_compile.subprocess.Popen",
                side_effect=FileNotFoundError() if missing else None,
            ),
        ):
            if not missing:
                mock_run.return_value = MagicMock(returncode=0)
            _compile_in_container(
                container_id=container_id,
                feature="splent_io/splent_feature_auth",
                watch=watch,
                production=False,
                workspace="/workspace",
                product="my_app",
            )

    def test_missing_bash_direct_mode_clean_error(self):
        # container_id=None → runs `bash -c ...` via proc.run.
        with pytest.raises(Exception) as exc:
            self._run(container_id=None, watch=False, missing=True)
        msg = str(exc.value)
        # proc.run translates FileNotFoundError → ClickException naming the tool.
        assert "bash" in msg
        assert "not installed or not on PATH" in msg
        assert _no_traceback(msg)

    def test_missing_docker_exec_mode_clean_error(self):
        # container_id set → runs `docker exec ...` via proc.run.
        with pytest.raises(Exception) as exc:
            self._run(container_id="abc123", watch=False, missing=True)
        msg = str(exc.value)
        assert "docker" in msg
        assert "not installed or not on PATH" in msg
        assert _no_traceback(msg)

    def test_missing_bash_watch_mode_clean_error(self):
        # watch path uses subprocess.Popen; hardening wraps it in try/except.
        with pytest.raises(Exception) as exc:
            self._run(container_id=None, watch=True, missing=True)
        msg = str(exc.value)
        assert "not installed or not on PATH" in msg
        assert _no_traceback(msg)

    def test_missing_docker_watch_mode_clean_error(self):
        with pytest.raises(Exception) as exc:
            self._run(container_id="abc123", watch=True, missing=True)
        msg = str(exc.value)
        assert "not installed or not on PATH" in msg
        assert _no_traceback(msg)


class TestCompileToolFails:
    def test_webpack_nonzero_exit_surfaced_cleanly(self, capsys):
        # webpack present but exits non-zero → caller reports, does not raise.
        with (
            patch(
                "splent_cli.commands.feature.feature_compile.os.path.exists",
                return_value=True,
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                return_value=MagicMock(returncode=1, stdout="", stderr=""),
            ),
        ):
            _compile_in_container(
                container_id=None,
                feature="splent_io/splent_feature_auth",
                watch=False,
                production=False,
                workspace="/workspace",
                product="my_app",
            )
        out = capsys.readouterr().out
        assert "Error compiling" in out
        assert "code 1" in out
        assert _no_traceback(out)

    def test_webpack_success_reports_done(self, capsys):
        with (
            patch(
                "splent_cli.commands.feature.feature_compile.os.path.exists",
                return_value=True,
            ),
            patch(
                "splent_cli.utils.proc.subprocess.run",
                return_value=MagicMock(returncode=0, stdout="", stderr=""),
            ),
        ):
            _compile_in_container(
                container_id=None,
                feature="splent_io/splent_feature_auth",
                watch=False,
                production=False,
                workspace="/workspace",
                product="my_app",
            )
        out = capsys.readouterr().out
        assert "Successfully compiled" in out


# ---------------------------------------------------------------------------
# feature:env — cp / shutil.copy guard + happy path
# ---------------------------------------------------------------------------


def _make_env_product(tmp_path, monkeypatch, *, with_template=True, existing_env=False):
    """Build a product whose feature symlink resolves to a docker/ dir.

    Returns the docker dir of the (real) feature target.
    """
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")
    monkeypatch.delenv("SPLENT_ENV", raising=False)

    product = tmp_path / "test_app"
    (product / "docker").mkdir(parents=True)
    # Entry has no explicit namespace so the bare feature_name passed on the
    # CLI matches via startswith(); org_safe then defaults to "splent_io".
    (product / "pyproject.toml").write_text(
        '[tool.splent]\nfeatures = ["splent_feature_auth@v1"]\n'
    )

    # Real feature target with a docker dir.
    real_feature = tmp_path / "splent_feature_auth_real"
    feat_docker = real_feature / "docker"
    feat_docker.mkdir(parents=True)
    if with_template:
        (feat_docker / ".env.example").write_text("KEY=value\n")
    if existing_env:
        (feat_docker / ".env").write_text("EXISTING=1\n")

    # Symlink inside product features tree (matches feature_env's lookup).
    link_dir = product / "features" / "splent_io"
    link_dir.mkdir(parents=True)
    link = link_dir / "splent_feature_auth@v1"
    link.symlink_to(real_feature)

    return feat_docker


class TestEnvCopyGuard:
    def test_copy_oserror_surfaced_cleanly(self, tmp_path, monkeypatch, runner):
        _make_env_product(tmp_path, monkeypatch)
        with patch(
            "splent_cli.commands.feature.feature_env.shutil.copy",
            side_effect=OSError("disk full"),
        ):
            result = runner.invoke(
                feature_env, ["splent_feature_auth", "--generate", "--dev"]
            )
        assert result.exit_code == 1
        assert "Failed to create" in result.output
        assert "disk full" in result.output
        assert _no_traceback(result.output)
        # No partial .env file left behind by the failed copy.
        feat_docker = tmp_path / "splent_feature_auth_real" / "docker"
        assert not (feat_docker / ".env").exists()

    def test_generate_copies_template_happy_path(self, tmp_path, monkeypatch, runner):
        feat_docker = _make_env_product(tmp_path, monkeypatch)
        result = runner.invoke(
            feature_env, ["splent_feature_auth", "--generate", "--dev"]
        )
        assert result.exit_code == 0
        env_file = feat_docker / ".env"
        assert env_file.exists()
        assert env_file.read_text() == "KEY=value\n"
        assert "Created" in result.output

    def test_existing_env_is_not_overwritten(self, tmp_path, monkeypatch, runner):
        feat_docker = _make_env_product(tmp_path, monkeypatch, existing_env=True)
        result = runner.invoke(
            feature_env, ["splent_feature_auth", "--generate", "--dev"]
        )
        assert result.exit_code == 0
        # Untouched.
        assert (feat_docker / ".env").read_text() == "EXISTING=1\n"
        assert "skipping" in result.output.lower()

    def test_no_template_warns_without_crash(self, tmp_path, monkeypatch, runner):
        _make_env_product(tmp_path, monkeypatch, with_template=False)
        result = runner.invoke(
            feature_env, ["splent_feature_auth", "--generate", "--dev"]
        )
        assert result.exit_code == 0
        assert "No .env template" in result.output
        assert _no_traceback(result.output)


# ---------------------------------------------------------------------------
# feature:git — git missing guard + arg passthrough forwards exit code
# ---------------------------------------------------------------------------


class TestGitToolGuard:
    def test_missing_git_surfaced_cleanly(self, tmp_path, monkeypatch, runner):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        cache = (
            tmp_path
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_auth"
        )
        cache.mkdir(parents=True)

        with patch(
            "splent_cli.utils.proc.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            result = runner.invoke(feature_git, ["auth", "status"])

        assert result.exit_code != 0
        assert "git" in result.stderr
        assert "not installed or not on PATH" in result.stderr
        assert _no_traceback(result.stderr)

    def test_passthrough_forwards_git_exit_code(self, tmp_path, monkeypatch, runner):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        cache = (
            tmp_path
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_auth"
        )
        cache.mkdir(parents=True)

        with patch(
            "splent_cli.utils.proc.subprocess.run",
            return_value=MagicMock(returncode=3, stdout="", stderr=""),
        ) as mock_run:
            result = runner.invoke(feature_git, ["auth", "log", "--oneline", "-5"])

        # Exit code forwarded verbatim.
        assert result.exit_code == 3
        # git invoked with the extra args passed through.
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "git"
        assert "log" in cmd
        assert "--oneline" in cmd
        assert "-5" in cmd
        assert _no_traceback(result.stderr)

    def test_passthrough_forwards_success_exit_code(
        self, tmp_path, monkeypatch, runner
    ):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.setenv("SPLENT_APP", "test_app")
        cache = (
            tmp_path
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_auth"
        )
        cache.mkdir(parents=True)

        with patch(
            "splent_cli.utils.proc.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ):
            result = runner.invoke(feature_git, ["auth", "status"])

        assert result.exit_code == 0
        assert _no_traceback(result.stderr)
