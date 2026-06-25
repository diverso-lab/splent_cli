"""Regression tests for the hardened feature_installer helpers.

Hardened behaviors under test (in priority order):
  * The workspace root is resolved via PathUtils.get_working_dir() / WORKING_DIR
    instead of a hardcoded ``/workspace`` — features are looked up relative to
    the *configured* workspace so this works on a host/dev box.
  * A single per-feature ``pip install`` failure is reported but does NOT abort
    the whole loop; remaining features are still attempted, and a summary of the
    failures is printed at the end.
  * A missing ``pip`` (FileNotFoundError → ClickException from proc.run) during
    install is handled the same way: reported, loop keeps going.

All subprocess / pip activity is mocked at the ``feature_installer.run`` boundary
(the module imports ``run`` into its own namespace), so no real pip / network is
touched. PathUtils.get_working_dir is patched to point at a pytest tmp_path.
"""
import click

import splent_cli.utils.feature_installer as fi


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_feature(workspace, feature_dir, package_name):
    """Create <workspace>/<feature_dir>/pyproject.toml declaring package_name."""
    fpath = workspace / feature_dir
    fpath.mkdir(parents=True, exist_ok=True)
    (fpath / "pyproject.toml").write_bytes(
        f'[project]\nname = "{package_name}"\n'.encode()
    )
    return fpath


class _FakeCompleted:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# _workspace_root — resolved from PathUtils/WORKING_DIR, not hardcoded
# ---------------------------------------------------------------------------

class TestWorkspaceRoot:
    def test_uses_pathutils_working_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        assert fi._workspace_root() == tmp_path

    def test_not_hardcoded_workspace_when_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        root = fi._workspace_root()
        assert str(root) != "/workspace"
        assert root == tmp_path


# ---------------------------------------------------------------------------
# get_package_name — pyproject parsing edge cases
# ---------------------------------------------------------------------------

class TestGetPackageName:
    def test_returns_name(self, tmp_path):
        fpath = _make_feature(tmp_path, "splent_feature_auth", "splent_feature_auth")
        assert fi.get_package_name(fpath) == "splent_feature_auth"

    def test_none_when_no_pyproject(self, tmp_path):
        fpath = tmp_path / "no_pyproject"
        fpath.mkdir()
        assert fi.get_package_name(fpath) is None

    def test_none_on_invalid_toml(self, tmp_path):
        fpath = tmp_path / "bad"
        fpath.mkdir()
        (fpath / "pyproject.toml").write_text("this is }{ not toml")
        assert fi.get_package_name(fpath) is None

    def test_none_when_name_missing(self, tmp_path):
        fpath = tmp_path / "noname"
        fpath.mkdir()
        (fpath / "pyproject.toml").write_bytes(b'[project]\nversion = "1.0"\n')
        assert fi.get_package_name(fpath) is None


# ---------------------------------------------------------------------------
# ensure_editable_features_installed — features located relative to workspace
# ---------------------------------------------------------------------------

class TestFeaturesResolvedRelativeToWorkspace:
    def test_install_targets_configured_workspace(self, tmp_path, monkeypatch):
        _make_feature(tmp_path, "splent_feature_auth", "splent_feature_auth")

        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            fi, "get_features_from_pyproject", lambda: ["splent_feature_auth"]
        )
        monkeypatch.setattr(fi, "get_installed_packages", lambda: set())

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return _FakeCompleted(0)

        monkeypatch.setattr(fi, "run", fake_run)

        fi.ensure_editable_features_installed()

        # pip install -e was called with a path UNDER the configured tmp workspace,
        # never a hardcoded /workspace.
        install_calls = [c for c in calls if "install" in c]
        assert len(install_calls) == 1
        target = install_calls[0][-1]
        assert target == str(tmp_path / "splent_feature_auth")
        assert not target.startswith("/workspace")

    def test_skips_feature_without_pyproject(self, tmp_path, monkeypatch, capsys):
        # feature directory does not exist under the workspace at all
        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            fi, "get_features_from_pyproject", lambda: ["splent_feature_ghost"]
        )
        monkeypatch.setattr(fi, "get_installed_packages", lambda: set())

        calls = []
        monkeypatch.setattr(
            fi, "run", lambda cmd, **kw: calls.append(list(cmd)) or _FakeCompleted(0)
        )

        fi.ensure_editable_features_installed()

        assert calls == []  # nothing installed
        out = capsys.readouterr().out
        assert "Skipping" in out
        # the message points at the configured workspace path, not /workspace
        assert str(tmp_path / "splent_feature_ghost") in out

    def test_already_installed_is_not_reinstalled(self, tmp_path, monkeypatch):
        _make_feature(tmp_path, "splent_feature_auth", "splent_feature_auth")
        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            fi, "get_features_from_pyproject", lambda: ["splent_feature_auth"]
        )
        monkeypatch.setattr(
            fi, "get_installed_packages", lambda: {"splent_feature_auth"}
        )

        calls = []
        monkeypatch.setattr(
            fi, "run", lambda cmd, **kw: calls.append(list(cmd)) or _FakeCompleted(0)
        )

        fi.ensure_editable_features_installed()
        assert calls == []  # no install attempted


# ---------------------------------------------------------------------------
# ensure_editable_features_installed — per-feature failure does not abort loop
# ---------------------------------------------------------------------------

class TestPerFeatureFailureDoesNotAbort:
    def test_one_failure_does_not_stop_others(self, tmp_path, monkeypatch, capsys):
        _make_feature(tmp_path, "splent_feature_a", "splent_feature_a")
        _make_feature(tmp_path, "splent_feature_b", "splent_feature_b")
        _make_feature(tmp_path, "splent_feature_c", "splent_feature_c")

        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            fi,
            "get_features_from_pyproject",
            lambda: ["splent_feature_a", "splent_feature_b", "splent_feature_c"],
        )
        monkeypatch.setattr(fi, "get_installed_packages", lambda: set())

        attempted = []

        def fake_run(cmd, **kwargs):
            attempted.append(cmd[-1])
            if "splent_feature_b" in cmd[-1]:
                raise click.ClickException("'pip' failed (exit 1).\nboom")
            return _FakeCompleted(0)

        monkeypatch.setattr(fi, "run", fake_run)

        # Must not raise: the loop swallows the per-feature ClickException.
        fi.ensure_editable_features_installed()

        # All three were attempted despite b failing in the middle.
        assert len(attempted) == 3
        assert any("splent_feature_a" in p for p in attempted)
        assert any("splent_feature_b" in p for p in attempted)
        assert any("splent_feature_c" in p for p in attempted)

        out = capsys.readouterr().out
        # Failure is reported and the summary names the failed package.
        assert "Failed to install splent_feature_b" in out
        assert "1 feature(s) failed to install" in out
        assert "splent_feature_b" in out
        # output is clean — no raw traceback leaked.
        assert "Traceback" not in out

    def test_pip_missing_is_handled_per_feature(self, tmp_path, monkeypatch, capsys):
        # proc.run translates a missing pip into a ClickException; the loop must
        # treat that like any other per-feature failure (report, keep going).
        _make_feature(tmp_path, "splent_feature_a", "splent_feature_a")
        _make_feature(tmp_path, "splent_feature_b", "splent_feature_b")

        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            fi,
            "get_features_from_pyproject",
            lambda: ["splent_feature_a", "splent_feature_b"],
        )
        monkeypatch.setattr(fi, "get_installed_packages", lambda: set())

        attempted = []

        def fake_run(cmd, **kwargs):
            attempted.append(cmd[-1])
            raise click.ClickException(
                "'pip' is not installed or not on PATH.\nInstall pip ..."
            )

        monkeypatch.setattr(fi, "run", fake_run)

        fi.ensure_editable_features_installed()

        assert len(attempted) == 2  # both still attempted
        out = capsys.readouterr().out
        assert "2 feature(s) failed to install" in out
        assert "Traceback" not in out

    def test_no_failure_summary_when_all_succeed(self, tmp_path, monkeypatch, capsys):
        _make_feature(tmp_path, "splent_feature_a", "splent_feature_a")
        monkeypatch.setattr(fi.PathUtils, "get_working_dir", lambda: str(tmp_path))
        monkeypatch.setattr(
            fi, "get_features_from_pyproject", lambda: ["splent_feature_a"]
        )
        monkeypatch.setattr(fi, "get_installed_packages", lambda: set())
        monkeypatch.setattr(fi, "run", lambda cmd, **kw: _FakeCompleted(0))

        fi.ensure_editable_features_installed()
        out = capsys.readouterr().out
        assert "failed to install" not in out


# ---------------------------------------------------------------------------
# get_installed_packages — parsing of pip list --format=freeze
# ---------------------------------------------------------------------------

class TestGetInstalledPackages:
    def test_parses_freeze_output(self, monkeypatch):
        monkeypatch.setattr(
            fi,
            "run",
            lambda cmd, **kw: _FakeCompleted(
                0, stdout="click==8.1.7\npytest==7.4.0\n"
            ),
        )
        pkgs = fi.get_installed_packages()
        assert pkgs == {"click", "pytest"}

    def test_ignores_lines_without_separator(self, monkeypatch):
        monkeypatch.setattr(
            fi,
            "run",
            lambda cmd, **kw: _FakeCompleted(
                0, stdout="click==8.1.7\n-e git+https://x#egg=foo\n"
            ),
        )
        pkgs = fi.get_installed_packages()
        assert pkgs == {"click"}
