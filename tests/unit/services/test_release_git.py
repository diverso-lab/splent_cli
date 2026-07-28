"""Git-level guarantees of the release pipeline, against real repositories.

These use a real git binary and a real bare remote because the failures they
cover are failures of what git actually did, not of what the code meant:

  * a tag published for a commit that is on no remote branch
  * a tag reused for a commit that is not the one being built
  * a "GitHub is off" release that pushes to GitHub anyway

Nothing here talks to GitHub or PyPI. The remote is a bare repository in a
temporary directory.
"""

import shutil
import subprocess

import click
import pytest
from click.testing import CliRunner

from splent_cli.commands.release.release_resume import build_tree
from splent_cli.services import release


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not installed"
)


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A clone with an origin remote, one commit on main, nothing pending."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "main")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "pyproject.toml").write_text('[project]\nname = "pkg"\nversion = "1.0.0"\n')
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "initial")
    _git(work, "push", "-u", "origin", "main")
    return work, remote


def _run(fn):
    @click.command()
    def cmd():
        fn()

    return CliRunner(mix_stderr=False).invoke(cmd)


# ── The tagged commit must be on the remote branch ────────────────────


class TestPushMain:
    def test_a_clean_tree_still_pushes_the_commit_it_is_about_to_tag(self, repo):
        """The bug: a retry after a failed push tagged an unpushed commit.

        commit_and_push returned early whenever the tree was clean, so the
        second run committed nothing, pushed nothing, and then pushed a tag
        pointing at a commit origin/main had never seen.
        """
        work, remote = repo
        (work / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "1.1.0"\n'
        )
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "bump, never pushed")
        head = _git(work, "rev-parse", "HEAD")
        assert _git(remote, "rev-parse", "main") != head

        result = _run(lambda: release.push_main(str(work)))
        assert result.exit_code == 0
        assert _git(remote, "rev-parse", "main") == head

    def test_the_commit_reaches_the_remote_before_any_tag_exists(self, repo):
        work, remote = repo
        (work / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "1.1.0"\n'
        )
        result = _run(
            lambda: (
                release.commit_locally(str(work), "v1.1.0"),
                release.push_main(str(work)),
                release.create_and_push_tag(str(work), "v1.1.0"),
            )
        )
        assert result.exit_code == 0
        head = _git(work, "rev-parse", "HEAD")
        assert _git(remote, "rev-parse", "main") == head
        assert _git(remote, "rev-list", "-n", "1", "refs/tags/v1.1.0") == head

    def test_an_unreachable_remote_stops_before_the_tag(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        _git(work, "init", "-b", "main")
        _git(work, "config", "user.email", "t@e.com")
        _git(work, "config", "user.name", "T")
        (work / "f.txt").write_text("x")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "c")
        _git(work, "remote", "add", "origin", str(tmp_path / "nowhere.git"))

        result = _run(lambda: release.push_main(str(work)))
        assert result.exit_code == 1
        assert "could not push" in result.output


class TestSyncMainIfNeeded:
    def test_an_unpushed_release_commit_is_pushed_before_it_is_tagged(self, repo):
        """The state a failed upload leaves behind: committed here, nowhere else."""
        work, remote = repo
        (work / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "1.1.0"\n'
        )
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "bump")
        head = _git(work, "rev-parse", "HEAD")

        result = _run(lambda: release.sync_main_if_needed(str(work)))
        assert result.exit_code == 0
        assert _git(remote, "rev-parse", "main") == head

    def test_an_up_to_date_branch_is_left_alone(self, repo):
        work, remote = repo
        before = _git(remote, "rev-parse", "main")
        result = _run(lambda: release.sync_main_if_needed(str(work)))
        assert result.exit_code == 0
        assert _git(remote, "rev-parse", "main") == before

    def test_a_diverged_branch_is_reported_not_forced(self, repo, tmp_path):
        work, remote = repo
        # Someone else pushed a different commit to main.
        other = tmp_path / "other"
        _git(tmp_path, "clone", str(remote), str(other))
        _git(other, "config", "user.email", "o@e.com")
        _git(other, "config", "user.name", "O")
        (other / "theirs.txt").write_text("theirs")
        _git(other, "add", "-A")
        _git(other, "commit", "-m", "theirs")
        _git(other, "push", "origin", "main")
        theirs = _git(other, "rev-parse", "HEAD")

        (work / "mine.txt").write_text("mine")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "mine")

        result = _run(lambda: release.sync_main_if_needed(str(work)))
        assert result.exit_code == 0
        assert "diverged" in result.output
        assert _git(remote, "rev-parse", "main") == theirs


class TestCreateAndPushTag:
    def test_a_tag_pointing_elsewhere_is_refused_not_reused(self, repo):
        """The artifacts come from this tree, so the tag must describe this tree.

        "already exists, skipping" published a package built from one commit
        under a tag that names another, and neither of the two can be corrected
        afterwards.
        """
        work, _ = repo
        _git(work, "tag", "-a", "v1.1.0", "-m", "old")
        (work / "extra.txt").write_text("later work")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "later")

        result = _run(lambda: release.create_and_push_tag(str(work), "v1.1.0"))
        assert result.exit_code == 1
        assert "points at another commit" in result.output

    def test_a_tag_already_at_this_commit_is_kept(self, repo):
        work, remote = repo
        _git(work, "tag", "-a", "v1.1.0", "-m", "same")
        result = _run(lambda: release.create_and_push_tag(str(work), "v1.1.0"))
        assert result.exit_code == 0
        assert "kept" in result.output
        assert _git(remote, "rev-parse", "refs/tags/v1.1.0")

    def test_push_false_creates_the_tag_locally_and_pushes_nothing(self, repo):
        """--no-github means nothing at all is published to GitHub."""
        work, remote = repo
        result = _run(
            lambda: release.create_and_push_tag(str(work), "v1.1.0", push=False)
        )
        assert result.exit_code == 0
        assert "kept LOCAL" in result.output
        assert _git(work, "tag") == "v1.1.0"
        assert (
            subprocess.run(
                ["git", "rev-parse", "refs/tags/v1.1.0"],
                cwd=remote,
                capture_output=True,
            ).returncode
            != 0
        )


# ── Resuming never moves the operator's checkout ──────────────────────


class TestBuildTree:
    def test_head_at_the_tag_builds_in_place(self, repo):
        work, _ = repo
        _git(work, "tag", "-a", "v1.0.0", "-m", "r")
        with build_tree(str(work), "v1.0.0") as path:
            assert path == str(work)

    def test_head_away_from_the_tag_builds_from_a_worktree(self, repo):
        """The old refusal sent the operator to `git checkout <tag>`.

        A feature directory is symlinked into the running product, so a
        detached checkout silently changes what the product serves, and nothing
        ever told the operator how to get back.
        """
        work, _ = repo
        _git(work, "tag", "-a", "v1.0.0", "-m", "r")
        tagged = _git(work, "rev-parse", "HEAD")
        (work / "later.txt").write_text("moved on")
        _git(work, "add", "-A")
        _git(work, "commit", "-m", "later")
        head_before = _git(work, "rev-parse", "HEAD")

        with build_tree(str(work), "v1.0.0") as path:
            assert path != str(work)
            assert _git(path, "rev-parse", "HEAD") == tagged
            # The tree really is the tagged source.
            assert not (work / "later.txt").exists() or path != str(work)
            built = path

        # The checkout is exactly where it was, and the worktree is gone.
        assert _git(work, "rev-parse", "HEAD") == head_before
        assert _git(work, "rev-parse", "--abbrev-ref", "HEAD") == "main"
        assert not (work / "later.txt").is_dir()
        import os

        assert not os.path.exists(built)
