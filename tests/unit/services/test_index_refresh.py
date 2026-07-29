"""
Publishing asks the marketplace index to rebuild itself.

The index lives in its own repository and used to hear about a release only
on its next scheduled run, hours later, so the only way to see something in
production sooner was to press the button by hand. Asking is best effort on
purpose: by the time it runs, the release is done and a tag and a PyPI
upload cannot be taken back, so nothing here may turn a finished release
into a failed command.
"""

import urllib.error

import pytest

from splent_cli.services import index_refresh


class _Response:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_it_posts_to_the_workflow_dispatch_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["auth"] = request.headers.get("Authorization")
        return _Response(204)

    monkeypatch.setattr(index_refresh.urllib.request, "urlopen", fake_urlopen)

    assert index_refresh.request_rebuild(quiet=True) is True
    assert seen["url"].endswith("/actions/workflows/build-index.yml/dispatches")
    assert seen["method"] == "POST"
    assert seen["auth"] == "Bearer t"


def test_without_a_token_it_does_nothing_and_says_so(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(
        index_refresh.urllib.request,
        "urlopen",
        lambda *a, **k: called.append(1),
    )
    assert index_refresh.request_rebuild(quiet=True) is False
    assert not called, "no token means no request at all"


@pytest.mark.parametrize("code", [403, 404, 500])
def test_a_refusal_never_raises(monkeypatch, code):
    """The release already happened; this cannot be what fails the command."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "no", {}, None)

    monkeypatch.setattr(index_refresh.urllib.request, "urlopen", fake_urlopen)
    assert index_refresh.request_rebuild(quiet=True) is False


def test_a_network_failure_never_raises(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    def fake_urlopen(request, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(index_refresh.urllib.request, "urlopen", fake_urlopen)
    assert index_refresh.request_rebuild(quiet=True) is False


def test_the_target_repository_can_be_pointed_elsewhere(monkeypatch):
    """A private org runs its own index, so the target is configurable."""
    monkeypatch.setattr(index_refresh, "INDEX_REPO", "acme/their_index")
    assert "acme/their_index" in index_refresh._api_url()
