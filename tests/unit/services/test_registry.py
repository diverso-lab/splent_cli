"""Unit tests for services/registry.py — the single remote-registry boundary.

All network is mocked at urllib.request.urlopen inside the registry module.
"""

import io
import json
import urllib.error

import pytest

from splent_cli.services import registry


def _response(payload):
    class _Resp:
        status = 200

        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp(json.dumps(payload).encode())


def _http_error(code, remaining=None):
    import email.message

    hdrs = email.message.Message()
    if remaining is not None:
        hdrs["X-RateLimit-Remaining"] = remaining
    return urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=code,
        msg="err",
        hdrs=hdrs,
        fp=io.BytesIO(b"{}"),
    )


# ── semver_sorted ───────────────────────────────────────────────────────────


class TestSemverSorted:
    def test_orders_by_semver_not_position(self):
        assert registry.semver_sorted(["v1.2.0", "v1.10.0", "v1.9.9"]) == [
            "v1.10.0",
            "v1.9.9",
            "v1.2.0",
        ]

    def test_drops_non_semver(self):
        assert registry.semver_sorted(["latest", "v2.0.0", "nightly"]) == ["v2.0.0"]

    def test_accepts_prefixless(self):
        assert registry.semver_sorted(["1.0.0", "v2.0.0"]) == ["v2.0.0", "1.0.0"]

    def test_empty(self):
        assert registry.semver_sorted([]) == []


# ── latest_semver_tag ───────────────────────────────────────────────────────


class TestLatestSemverTagStrictness:
    def test_strict_raises_where_quiet_answers_none(self, monkeypatch):
        """ "No tags found" is a claim. It must not come from a failed request."""

        def _raise(*a, **k):
            raise _http_error(401)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.latest_semver_tag("org", "repo") is None
        with pytest.raises(registry.RegistryError):
            registry.latest_semver_tag("org", "repo", strict=True)

    def test_strict_raises_on_a_missing_repository(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError):
            registry.latest_semver_tag("org", "gone", strict=True)

    def test_strict_still_answers_none_for_a_repo_with_no_tags(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response([])
        )
        assert registry.latest_semver_tag("org", "repo", strict=True) is None


class TestLatestSemverTag:
    def test_returns_highest_semver(self, monkeypatch):
        payload = [{"name": "v1.2.0"}, {"name": "v1.10.0"}]
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.latest_semver_tag("org", "repo") == "v1.10.0"

    def test_falls_back_to_first_tag_when_no_semver(self, monkeypatch):
        payload = [{"name": "release-a"}, {"name": "release-b"}]
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.latest_semver_tag("org", "repo") == "release-a"

    def test_none_on_empty(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response([])
        )
        assert registry.latest_semver_tag("org", "repo") is None

    def test_none_on_error(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(403, remaining="0")

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.latest_semver_tag("org", "repo") is None


# ── error mapping ───────────────────────────────────────────────────────────


class TestErrorMapping:
    def test_404_is_none(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.github_json("https://api.github.com/x") is None

    def test_403_with_zero_remaining_is_rate_limited(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(403, remaining="0")

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.github_json("https://api.github.com/x")
        assert exc.value.rate_limited is True
        assert exc.value.status == 403

    def test_403_without_header_is_forbidden_not_rate_limited(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(403)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.github_json("https://api.github.com/x")
        assert exc.value.rate_limited is False
        assert exc.value.status == 403

    def test_429_is_rate_limited(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(429)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.github_json("https://api.github.com/x")
        assert exc.value.rate_limited is True


# ── list_semver_tags ────────────────────────────────────────────────────────


class TestListSemverTags:
    def test_limit(self, monkeypatch):
        payload = [{"name": f"v1.{i}.0"} for i in range(20)]
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        result = registry.list_semver_tags("org", "repo", limit=3)
        assert result == ["v1.19.0", "v1.18.0", "v1.17.0"]

    def test_quiet_on_error(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(500)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.list_semver_tags("org", "repo") == []

    def test_a_repository_that_does_not_exist_raises(self, monkeypatch):
        """A renamed org or a mistyped remote is not a repo with no tags.

        list_tags mapped the repository 404 to an empty list, so a report that
        asked "which tags exist" was told "none" and counted the package as
        never released.
        """

        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.list_tags("org", "gone")
        assert exc.value.status == 404
        # The quiet wrapper keeps its historical behavior.
        assert registry.list_semver_tags("org", "gone") == []

    def test_an_empty_tag_list_is_still_an_empty_list(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response([])
        )
        assert registry.list_tags("org", "repo") == []


class TestListReleaseTags:
    """One call answers "which tags have a release" for every tag."""

    def test_collects_the_tag_names(self, monkeypatch):
        payload = [{"tag_name": "v1.1.0"}, {"tag_name": "v1.0.0"}]
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.list_release_tags("org", "repo") == {"v1.1.0", "v1.0.0"}

    def test_no_releases_is_an_empty_set(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response([])
        )
        assert registry.list_release_tags("org", "repo") == set()

    def test_failure_raises_instead_of_answering_none(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(401)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError):
            registry.list_release_tags("org", "repo")


# ── PyPI ────────────────────────────────────────────────────────────────────


class TestPyPI:
    def test_versions_sorted_by_upload_time(self, monkeypatch):
        payload = {
            "releases": {
                "1.0.0": [{"upload_time": "2026-01-01T00:00:00"}],
                "1.1.0": [{"upload_time": "2026-06-01T00:00:00"}],
            }
        }
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.pypi_versions("pkg") == ["1.1.0", "1.0.0"]

    def test_versions_quiet_on_404(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.pypi_versions("pkg") == []

    def test_strict_versions_raise_on_429_instead_of_answering_nothing(
        self, monkeypatch
    ):
        """HTTPError subclasses URLError, so 429 used to read as "no releases".

        Every tag of every package then looked like a divergence.
        """

        def _raise(*a, **k):
            raise _http_error(429)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.pypi_versions("pkg") == []
        with pytest.raises(registry.RegistryError) as exc:
            registry.pypi_versions("pkg", strict=True)
        assert exc.value.rate_limited is True

    def test_strict_versions_still_answer_empty_on_404(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.pypi_versions("pkg", strict=True) == []

    def test_strict_versions_raise_on_a_dead_network(self, monkeypatch):
        def _raise(*a, **k):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError):
            registry.pypi_versions("pkg", strict=True)


# ── PyPI primitives the release gate depends on ─────────────────────────────


def _pypi_error(code, *, retry_after=None, body=b"{}"):
    import email.message

    hdrs = email.message.Message()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        url="https://upload.pypi.org/legacy/",
        code=code,
        msg="err",
        hdrs=hdrs,
        fp=io.BytesIO(body),
    )


class TestPyPIUploadProbe:
    """The probe reports what PyPI said. It never publishes anything."""

    def test_400_means_credentials_accepted(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(400)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        probe = registry.pypi_upload_probe("__token__", "pypi-x")
        assert probe.status == 400
        assert probe.rate_limited is False

    def test_403_is_credentials_not_rate_limit(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(403)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        probe = registry.pypi_upload_probe("__token__", "bad")
        assert probe.status == 403
        assert probe.rate_limited is False

    def test_429_is_rate_limit_and_keeps_retry_after(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(429, retry_after="600")

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        probe = registry.pypi_upload_probe("__token__", "pypi-x")
        assert probe.status == 429
        assert probe.rate_limited is True
        assert probe.retry_after == "600"

    def test_posts_to_the_upload_endpoint_without_a_file(self, monkeypatch):
        captured = {}

        def _capture(req, *a, **k):
            captured["url"] = req.full_url
            captured["body"] = req.data
            raise _pypi_error(400)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _capture)
        registry.pypi_upload_probe("__token__", "pypi-x")
        assert captured["url"] == registry.PYPI_UPLOAD_URL
        # ":action=file_upload" and nothing resembling a distribution file.
        assert b"file_upload" in captured["body"]
        assert b"filename=" not in captured["body"]

    def test_testpypi_endpoint(self, monkeypatch):
        captured = {}

        def _capture(req, *a, **k):
            captured["url"] = req.full_url
            raise _pypi_error(400)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _capture)
        registry.pypi_upload_probe("u", "p", test=True)
        assert captured["url"] == registry.TEST_PYPI_UPLOAD_URL

    def test_network_failure_raises_instead_of_verdict(self, monkeypatch):
        def _raise(*a, **k):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError):
            registry.pypi_upload_probe("u", "p")

    def test_a_named_probe_sends_the_project_name(self, monkeypatch):
        """The limiter that refused these releases is keyed on the project name.

        A probe that never sends one cannot see it, which is why a gate built
        on a nameless probe answered "ready" for all 13 divergent features.
        """
        captured = {}

        def _capture(req, *a, **k):
            captured["body"] = req.data
            raise _pypi_error(400)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _capture)
        probe = registry.pypi_upload_probe(
            "__token__", "pypi-x", package="splent_feature_team", version="0.2.1"
        )
        body = captured["body"]
        assert b"splent_feature_team" in body
        assert b"0.2.1" in body
        assert b"metadata_version" in body
        assert probe.named is True
        # Still no distribution file, so still nothing publishable.
        assert b"filename=" not in body

    def test_a_nameless_probe_says_it_was_nameless(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(400)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.pypi_upload_probe("u", "p").named is False

    def test_the_version_prefix_is_stripped_for_the_metadata(self, monkeypatch):
        captured = {}

        def _capture(req, *a, **k):
            captured["body"] = req.data
            raise _pypi_error(400)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _capture)
        registry.pypi_upload_probe("u", "p", package="pkg", version="v1.2.3")
        assert b"1.2.3" in captured["body"]
        assert b"v1.2.3" not in captured["body"]


class TestDockerHub:
    def test_login_200_is_accepted(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request,
            "urlopen",
            lambda *a, **k: _response({"token": "t"}),
        )
        probe = registry.dockerhub_login_probe("u", "p")
        assert probe.status == 200
        assert probe.rate_limited is False

    def test_login_401_is_credentials(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(401)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        probe = registry.dockerhub_login_probe("u", "p")
        assert probe.status == 401
        assert probe.rate_limited is False

    def test_login_429_is_a_rate_limit(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(429)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.dockerhub_login_probe("u", "p").rate_limited is True

    def test_tag_lookup_404_is_absent(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.dockerhub_tag_exists("me", "app", "1.0.0") is False

    def test_tag_lookup_failure_raises_instead_of_guessing(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(500)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError):
            registry.dockerhub_tag_exists("me", "app", "1.0.0")


class TestPyPIProjectExists:
    def test_404_means_this_upload_creates_the_project(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.pypi_project_exists("brand_new") is False

    def test_200_means_the_project_exists(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response({})
        )
        assert registry.pypi_project_exists("splent_feature_auth") is True

    def test_429_raises_rate_limited_instead_of_guessing(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(429)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        with pytest.raises(registry.RegistryError) as exc:
            registry.pypi_project_exists("pkg")
        assert exc.value.rate_limited is True


class TestPyPIVersionExistsStrict:
    def test_strict_429_raises_rather_than_reporting_published(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(429)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        # Quiet mode keeps its historical "any non-404 counts as exists".
        assert registry.pypi_version_exists("pkg", "1.0.0") is True
        with pytest.raises(registry.RegistryError) as exc:
            registry.pypi_version_exists("pkg", "1.0.0", strict=True)
        assert exc.value.rate_limited is True

    def test_strict_404_is_absent(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.pypi_version_exists("pkg", "1.0.0", strict=True) is False


# ── GitHub primitives the release gate depends on ───────────────────────────


class TestGitHubGateHelpers:
    def test_rate_limit_reads_core_bucket(self, monkeypatch):
        payload = {"resources": {"core": {"limit": 5000, "remaining": 0, "reset": 99}}}
        monkeypatch.setattr(
            registry.urllib.request, "urlopen", lambda *a, **k: _response(payload)
        )
        assert registry.github_rate_limit("tok") == {
            "limit": 5000,
            "remaining": 0,
            "reset": 99,
        }

    def test_tag_exists_true(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request,
            "urlopen",
            lambda *a, **k: _response({"ref": "refs/tags/v1.0.0"}),
        )
        assert registry.github_tag_exists("org", "repo", "v1.0.0") is True

    def test_tag_exists_false_on_404(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(404)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        assert registry.github_tag_exists("org", "repo", "v1.0.0") is False

    def test_tag_exists_rejects_prefix_match(self, monkeypatch):
        # GitHub answers an ambiguous ref with a list of everything matching.
        monkeypatch.setattr(
            registry.urllib.request,
            "urlopen",
            lambda *a, **k: _response([{"ref": "refs/tags/v1.0.0-rc1"}]),
        )
        assert registry.github_tag_exists("org", "repo", "v1.0.0") is False


class TestGitHubCreateRelease:
    def test_created(self, monkeypatch):
        monkeypatch.setattr(
            registry.urllib.request,
            "urlopen",
            lambda *a, **k: _response({"html_url": "https://gh/r/1"}),
        )
        result = registry.github_create_release(
            "org", "repo", tag="v1.0.0", name="Release", body="b", token="t"
        )
        assert result.created is True
        assert result.url == "https://gh/r/1"

    def test_already_exists_is_not_a_failure(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(422, body=b'{"errors":[{"code":"already_exists"}]}')

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        result = registry.github_create_release(
            "org", "repo", tag="v1.0.0", name="R", body="b", token="t"
        )
        assert result.created is False
        assert result.already_exists is True

    def test_401_is_reported_not_swallowed(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(401)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        result = registry.github_create_release(
            "org", "repo", tag="v1.0.0", name="R", body="b", token="t"
        )
        assert result.created is False
        assert result.already_exists is False
        assert result.status == 401

    def test_429_is_flagged_as_rate_limited(self, monkeypatch):
        def _raise(*a, **k):
            raise _pypi_error(429)

        monkeypatch.setattr(registry.urllib.request, "urlopen", _raise)
        result = registry.github_create_release(
            "org", "repo", tag="v1.0.0", name="R", body="b", token="t"
        )
        assert result.rate_limited is True
