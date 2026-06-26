"""
Tests for the hardened hot-reload helpers in
``splent_cli.utils.feature_utils``: ``hot_reinstall`` and ``hot_uninstall``.

Hardened behaviors (covered first):
  * A non-zero ``docker exec`` (pip install/uninstall) return code is surfaced
    as ``SystemExit`` with a clean red message — NOT silently ignored.
  * A non-zero ``docker exec`` for the post-install ``touch`` warns (yellow)
    but does not abort.
  * When no compose file is resolved, the helper WARNS and returns (skips)
    instead of touching docker at all.
  * When no main container is found, the helper WARNS and returns (skips)
    instead of touching docker at all.

No real docker/network: the only boundary that shells out is
``splent_cli.utils.proc.subprocess.run`` (feature_utils calls ``proc.run``
with ``check=False``), and ``compose.resolve_file`` / ``find_main_container``
are mocked.
"""

import subprocess

import pytest

from splent_cli.utils import feature_utils


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.fixture
def fake_compose(monkeypatch):
    """Patch the compose service so a compose file + container are 'found'.

    feature_utils imports compose lazily (``from splent_cli.services import
    compose``) so patching the attributes on the service module is what the
    function sees at call time.
    """
    from splent_cli.services import compose

    monkeypatch.setattr(
        compose, "resolve_file", lambda *a, **k: "/p/docker/compose.dev.yml"
    )
    monkeypatch.setattr(compose, "project_name", lambda *a, **k: "proj_dev")
    monkeypatch.setattr(compose, "find_main_container", lambda *a, **k: "cid123")
    return compose


# --------------------------------------------------------------------------- #
# Hardened: non-zero docker exec (pip) is surfaced, not swallowed
# --------------------------------------------------------------------------- #


class TestPipFailureSurfaced:
    def test_reinstall_pip_failure_raises_system_exit(
        self, monkeypatch, capsys, fake_compose
    ):
        # First docker exec (pip install) fails.
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return _completed(returncode=2, stderr="boom: could not install")

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", fake_run)

        with pytest.raises(SystemExit) as exc:
            feature_utils.hot_reinstall("/p", "/workspace/splent_feature_auth", "auth")
        assert exc.value.code == 1

        out = capsys.readouterr().out
        # Failure surfaced with the exit code and the captured detail.
        assert "failed" in out
        assert "exit 2" in out
        assert "boom: could not install" in out
        # No raw traceback / CalledProcessError leaked.
        assert "Traceback" not in out
        assert "CalledProcessError" not in out
        # It must NOT have proceeded to the touch step after pip failed.
        assert len(calls) == 1

    def test_uninstall_pip_failure_raises_system_exit(
        self, monkeypatch, capsys, fake_compose
    ):
        def fake_run(cmd, *args, **kwargs):
            return _completed(returncode=3, stderr="cannot uninstall")

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", fake_run)

        with pytest.raises(SystemExit) as exc:
            feature_utils.hot_uninstall("/p", "splent_feature_auth")
        assert exc.value.code == 1

        out = capsys.readouterr().out
        assert "failed" in out
        assert "exit 3" in out
        assert "cannot uninstall" in out
        assert "Traceback" not in out

    def test_reinstall_pip_failure_with_only_stdout_detail(
        self, monkeypatch, capsys, fake_compose
    ):
        # Detail can come from stdout when stderr is empty.
        def fake_run(cmd, *args, **kwargs):
            return _completed(returncode=1, stdout="stdout detail here", stderr="")

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", fake_run)

        with pytest.raises(SystemExit):
            feature_utils.hot_reinstall("/p", "/workspace/x", "x")

        out = capsys.readouterr().out
        assert "stdout detail here" in out


# --------------------------------------------------------------------------- #
# Hardened: post-install touch failure warns but does not abort
# --------------------------------------------------------------------------- #


class TestTouchFailureWarnsButContinues:
    def test_reinstall_touch_failure_warns_no_raise(
        self, monkeypatch, capsys, fake_compose
    ):
        # pip ok (rc 0), touch fails (rc 1).
        rcs = iter([0, 1])

        def fake_run(cmd, *args, **kwargs):
            return _completed(returncode=next(rcs), stderr="touch: no such file")

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", fake_run)

        # Must NOT raise: a touch failure only warns.
        feature_utils.hot_reinstall("/p", "/workspace/x", "x")

        out = capsys.readouterr().out
        assert "could not touch" in out
        assert "restart the web container manually" in out
        assert "Traceback" not in out

    def test_uninstall_touch_failure_warns_no_raise(
        self, monkeypatch, capsys, fake_compose
    ):
        rcs = iter([0, 5])

        def fake_run(cmd, *args, **kwargs):
            return _completed(returncode=next(rcs))

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", fake_run)

        feature_utils.hot_uninstall("/p", "splent_feature_auth")

        out = capsys.readouterr().out
        assert "could not touch" in out
        assert "Traceback" not in out


# --------------------------------------------------------------------------- #
# Hardened: missing compose file / container WARN rather than silently return
# --------------------------------------------------------------------------- #


class TestMissingComposeWarns:
    def test_reinstall_no_compose_file_warns_and_skips_docker(
        self, monkeypatch, capsys
    ):
        from splent_cli.services import compose

        monkeypatch.setattr(compose, "resolve_file", lambda *a, **k: None)

        # Guard: docker boundary must never be hit when there is no compose file.
        def boom(*a, **k):  # pragma: no cover - asserts it is not called
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", boom)

        feature_utils.hot_reinstall("/some/myproduct", "/workspace/x", "x")

        out = capsys.readouterr().out
        assert "no compose file" in out
        assert "myproduct" in out
        assert "skipping" in out

    def test_uninstall_no_compose_file_warns_and_skips_docker(
        self, monkeypatch, capsys
    ):
        from splent_cli.services import compose

        monkeypatch.setattr(compose, "resolve_file", lambda *a, **k: None)
        monkeypatch.setattr(
            "splent_cli.utils.proc.subprocess.run",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("docker should not run")
            ),
        )

        feature_utils.hot_uninstall("/some/myproduct", "splent_feature_auth")

        out = capsys.readouterr().out
        assert "no compose file" in out
        assert "skipping" in out


class TestMissingContainerWarns:
    def test_reinstall_no_container_warns_and_skips_docker(self, monkeypatch, capsys):
        from splent_cli.services import compose

        monkeypatch.setattr(
            compose, "resolve_file", lambda *a, **k: "/p/docker/compose.yml"
        )
        monkeypatch.setattr(compose, "project_name", lambda *a, **k: "proj")
        monkeypatch.setattr(compose, "find_main_container", lambda *a, **k: None)
        monkeypatch.setattr(
            "splent_cli.utils.proc.subprocess.run",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("docker should not run")
            ),
        )

        feature_utils.hot_reinstall("/some/myproduct", "/workspace/x", "x")

        out = capsys.readouterr().out
        assert "not running" in out
        assert "skipping" in out

    def test_uninstall_no_container_warns_and_skips_docker(self, monkeypatch, capsys):
        from splent_cli.services import compose

        monkeypatch.setattr(
            compose, "resolve_file", lambda *a, **k: "/p/docker/compose.yml"
        )
        monkeypatch.setattr(compose, "project_name", lambda *a, **k: "proj")
        monkeypatch.setattr(compose, "find_main_container", lambda *a, **k: None)
        monkeypatch.setattr(
            "splent_cli.utils.proc.subprocess.run",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("docker should not run")
            ),
        )

        feature_utils.hot_uninstall("/some/myproduct", "splent_feature_auth")

        out = capsys.readouterr().out
        assert "not running" in out
        assert "skipping" in out


# --------------------------------------------------------------------------- #
# Happy paths
# --------------------------------------------------------------------------- #


class TestHappyPath:
    def test_reinstall_success_runs_pip_then_touch(
        self, monkeypatch, capsys, fake_compose
    ):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return _completed(returncode=0)

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", fake_run)

        feature_utils.hot_reinstall(
            "/some/myproduct", "/workspace/splent_feature_auth", "auth"
        )

        # Two docker exec invocations: pip install, then touch.
        assert len(calls) == 2
        pip_call, touch_call = calls
        assert pip_call[:3] == ["docker", "exec", "cid123"]
        assert "pip install" in pip_call[-1]
        assert "/workspace/splent_feature_auth" in pip_call[-1]
        # touch targets the product's __init__.py to trigger reload.
        assert "touch" in touch_call[-1]
        assert "/workspace/myproduct/src/myproduct/__init__.py" in touch_call[-1]

        out = capsys.readouterr().out
        assert "reinstalling in web container" in out
        assert "Traceback" not in out

    def test_uninstall_success_runs_pip_uninstall_then_touch(
        self, monkeypatch, capsys, fake_compose
    ):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return _completed(returncode=0)

        monkeypatch.setattr("splent_cli.utils.proc.subprocess.run", fake_run)

        feature_utils.hot_uninstall("/some/myproduct", "splent_feature_auth")

        assert len(calls) == 2
        pip_call, touch_call = calls
        assert "pip uninstall" in pip_call[-1]
        assert "splent_feature_auth" in pip_call[-1]
        assert "touch" in touch_call[-1]

        out = capsys.readouterr().out
        assert "uninstalling from web container" in out
        assert "Traceback" not in out
