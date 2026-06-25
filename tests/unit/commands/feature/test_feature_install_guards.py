"""Tests for feature:install — hardened guards around the attach/add and
env subprocess steps.

The command shells out via the ``splent_cli.utils.proc.run`` wrapper (imported
into the module namespace as ``feature_install.run``).  These tests assert the
hardened behaviour: when a sub-step (attach / add / env generate / env merge)
returns a non-zero exit code the install ABORTS with the captured stderr and
NEVER reports success; and a missing docker/tool surfaces a clean message
instead of a traceback.

All subprocess / network boundaries are mocked; no real git/docker/network is
used.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from splent_cli.commands.feature.feature_install import feature_install


def _ok(stdout="", stderr=""):
    """A successful CompletedProcess-like result."""
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr="boom", stdout="", returncode=1):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Workspace + active product configured via env vars."""
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", "test_app")
    monkeypatch.setenv("SPLENT_ENV", "dev")
    return tmp_path


def _make_editable_feature(workspace, feature_name="splent_feature_nginx"):
    """Create the editable feature dir (no pyproject -> deps check skipped)."""
    fdir = workspace / feature_name
    fdir.mkdir(parents=True, exist_ok=True)
    return fdir


# ---------------------------------------------------------------------------
# Hardened behaviour: a failing sub-step aborts and never claims success
# ---------------------------------------------------------------------------


class TestAttachFailureAborts:
    def test_attach_nonzero_aborts_with_stderr(self, env, monkeypatch):
        """Pinned mode: a non-zero `feature:attach` must abort with its stderr
        and must NOT print the 'installed.' success line."""
        # Pre-create the pinned cache dir so no clone is attempted, and leave
        # no feature pyproject so the dependency check is skipped.
        cache_dir = (
            env
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_nginx@v1.0.0"
        )
        cache_dir.mkdir(parents=True)

        def fake_run(cmd, *a, **k):
            if "feature:attach" in cmd:
                return _fail(stderr="attach exploded: bad contract")
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                [
                    "splent-io/splent_feature_nginx",
                    "--pinned",
                    "--version",
                    "v1.0.0",
                ],
            )

        assert result.exit_code != 0
        assert "Attach failed" in result.output
        assert "attach exploded: bad contract" in result.output
        # Did NOT falsely report success.
        assert "installed." not in result.output
        # Clean — no raw traceback leaked.
        assert "Traceback" not in result.output
        assert "Traceback" not in (result.stderr or "")

    def test_attach_failure_does_not_run_env_steps(self, env, monkeypatch):
        """If attach fails, env generate/merge must never be invoked."""
        cache_dir = (
            env
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_nginx@v1.0.0"
        )
        cache_dir.mkdir(parents=True)

        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            if "feature:attach" in cmd:
                return _fail(stderr="nope")
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                [
                    "splent-io/splent_feature_nginx",
                    "--pinned",
                    "--version",
                    "v1.0.0",
                ],
            )

        assert result.exit_code != 0
        # product:env must not have been reached.
        assert not any("product:env" in c for c in calls)


class TestAddFailureAborts:
    def test_add_nonzero_aborts_with_stderr(self, env, monkeypatch):
        """Editable mode: a non-zero `feature:add` must abort with its stderr
        and must NOT print the success line."""
        _make_editable_feature(env)

        def fake_run(cmd, *a, **k):
            if "feature:add" in cmd:
                return _fail(stderr="add exploded: duplicate entry")
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )

        assert result.exit_code != 0
        assert "Add failed" in result.output
        assert "add exploded: duplicate entry" in result.output
        assert "installed." not in result.output
        assert "Traceback" not in result.output

    def test_add_failure_falls_back_to_stdout(self, env, monkeypatch):
        """When stderr is empty the captured stdout is surfaced instead."""
        _make_editable_feature(env)

        def fake_run(cmd, *a, **k):
            if "feature:add" in cmd:
                return _fail(stderr="", stdout="stdout diagnostic message")
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )

        assert result.exit_code != 0
        assert "stdout diagnostic message" in result.output
        assert "installed." not in result.output


class TestEnvStepFailureAborts:
    def test_env_generate_failure_aborts(self, env, monkeypatch):
        """A non-zero `product:env --generate` must abort install."""
        _make_editable_feature(env)

        def fake_run(cmd, *a, **k):
            if "product:env" in cmd and "--generate" in cmd:
                return _fail(stderr="env gen exploded")
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )

        assert result.exit_code != 0
        assert "Env generate failed" in result.output
        assert "env gen exploded" in result.output
        assert "installed." not in result.output

    def test_env_merge_failure_aborts(self, env, monkeypatch):
        """A non-zero `product:env --merge` must abort install."""
        _make_editable_feature(env)

        def fake_run(cmd, *a, **k):
            if "product:env" in cmd and "--merge" in cmd:
                return _fail(stderr="env merge exploded")
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )

        assert result.exit_code != 0
        assert "Env merge failed" in result.output
        assert "env merge exploded" in result.output
        assert "installed." not in result.output


# ---------------------------------------------------------------------------
# Hardened behaviour: missing tool surfaces a clean message (no traceback)
# ---------------------------------------------------------------------------


class TestMissingToolCleanMessage:
    def test_missing_git_clone_clean_message(self, env, monkeypatch):
        """Editable mode with no local dir clones via the `run` wrapper.

        If git is missing the wrapper raises a ClickException ('... is not
        installed or not on PATH'); the user must see a clean message, never a
        FileNotFoundError traceback.
        """
        # No editable dir -> the command will attempt a git clone.
        from splent_cli.utils.proc import run as real_run

        def fake_run(cmd, *a, **k):
            # Simulate the proc wrapper's behaviour for a missing tool by
            # delegating to the real wrapper which translates FileNotFoundError
            # into a ClickException.
            if cmd[:2] == ["git", "clone"]:
                with patch(
                    "splent_cli.utils.proc.subprocess.run",
                    side_effect=FileNotFoundError(),
                ):
                    return real_run(cmd, *a, **k)
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_clone._get_latest_tag",
            return_value="v1.0.0",
        ), patch(
            "splent_cli.commands.feature.feature_clone._build_repo_url",
            return_value=("https://example.invalid/r.git", "display"),
        ), patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )

        assert result.exit_code != 0
        assert "git" in result.output.lower() or "git" in (result.stderr or "").lower()
        # A ClickException prints a clean message, not a traceback.
        assert "Traceback" not in result.output
        assert "Traceback" not in (result.stderr or "")
        assert "FileNotFoundError" not in result.output
        assert "installed." not in result.output

    def test_missing_docker_clean_message_in_docker_step(self, env, monkeypatch):
        """If the feature ships a compose file, the docker step calls
        require_docker(); a missing docker binary yields a clean ClickException,
        not a traceback."""
        feature_name = "splent_feature_nginx"
        _make_editable_feature(env, feature_name)
        # Give the feature a docker/ dir with a compose file so resolve_file
        # returns a path and the docker step runs.
        docker_dir = env / feature_name / "docker"
        docker_dir.mkdir(parents=True)
        (docker_dir / "docker-compose.dev.yml").write_text("services: {}\n")

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=lambda cmd, *a, **k: _ok(),
        ), patch(
            "splent_cli.utils.proc.shutil.which",
            return_value=None,  # docker not on PATH
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )

        assert result.exit_code != 0
        assert "docker" in result.output.lower() or "docker" in (
            result.stderr or ""
        ).lower()
        assert "Traceback" not in result.output
        assert "Traceback" not in (result.stderr or "")


# ---------------------------------------------------------------------------
# Core happy paths
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_editable_install_success(self, env, monkeypatch):
        """All sub-steps succeed, no feature compose file -> success line."""
        _make_editable_feature(env)

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=lambda cmd, *a, **k: _ok(),
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )

        assert result.exit_code == 0, result.output
        assert "installed." in result.output
        assert "Traceback" not in result.output

    def test_pinned_install_success_uses_cache(self, env, monkeypatch):
        """Pinned mode with the version already cached skips clone and
        succeeds end-to-end."""
        cache_dir = (
            env
            / ".splent_cache"
            / "features"
            / "splent_io"
            / "splent_feature_nginx@v1.0.0"
        )
        cache_dir.mkdir(parents=True)

        calls = []

        def fake_run(cmd, *a, **k):
            calls.append(list(cmd))
            return _ok()

        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=fake_run,
        ):
            result = runner.invoke(
                feature_install,
                [
                    "splent-io/splent_feature_nginx",
                    "--pinned",
                    "--version",
                    "v1.0.0",
                ],
            )

        assert result.exit_code == 0, result.output
        assert "installed." in result.output
        assert "already cached" in result.output
        # No clone was attempted (cache present).
        assert not any("feature:clone" in c for c in calls)
        # Attach was attempted.
        assert any("feature:attach" in c for c in calls)

    def test_requires_app(self, env, monkeypatch):
        """With no SPLENT_APP the command aborts cleanly via context.require_app."""
        monkeypatch.delenv("SPLENT_APP", raising=False)
        runner = CliRunner(mix_stderr=False)
        with patch(
            "splent_cli.commands.feature.feature_install.run",
            side_effect=lambda cmd, *a, **k: _ok(),
        ):
            result = runner.invoke(
                feature_install,
                ["splent-io/splent_feature_nginx", "--editable"],
            )
        assert result.exit_code != 0
        assert "SPLENT_APP" in result.output
        assert "Traceback" not in result.output
