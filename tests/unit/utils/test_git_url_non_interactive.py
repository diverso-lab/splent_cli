"""
Cloning tries several candidate URLs, so a wrong one has to fail fast.

Over HTTPS, GitHub answers a repository that does not exist and one that is
private-and-unauthenticated the same way: with a username prompt. A machine
with no SSH key therefore hung on the first candidate spelling instead of
moving to the next one, which is how a public repository looked private.
"""

from splent_cli.utils.git_url import _non_interactive_env


def test_git_is_told_not_to_ask_for_credentials():
    env = _non_interactive_env()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"] == ""
    assert env["SSH_ASKPASS"] == ""


def test_ssh_runs_in_batch_mode(monkeypatch):
    """Otherwise an unknown host key or a passphrase stops the command.

    The variable is cleared first because the next test down proves an
    existing one is respected: without this the two disagree whenever the
    shell running the suite happens to export its own, which is exactly what
    a release run does when it is given a deploy key.
    """
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    command = _non_interactive_env()["GIT_SSH_COMMAND"]
    assert "BatchMode=yes" in command
    assert "ConnectTimeout" in command


def test_an_existing_ssh_command_is_respected(monkeypatch):
    """Someone who configured their own ssh invocation keeps it."""
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /custom/key")
    assert _non_interactive_env()["GIT_SSH_COMMAND"] == "ssh -i /custom/key"


def test_the_rest_of_the_environment_survives(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "sentinel")
    assert _non_interactive_env()["GITHUB_TOKEN"] == "sentinel"
