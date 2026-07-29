"""Adversarial tests for spl:publish — publishing the local UVL model.

The command relays through the SPLENT marketplace, which holds the one UVLHub
key. It used to talk to UVLHub itself with the DEVELOPER's key and, when that
key was missing, printed instructions for generating one at
{UVLHUB_URL}/developer/api-keys — the exact credential the marketplace exists
so that nobody has to hold. The relay endpoint was live the whole time and had
no client, so the loop the two halves were built to close was never closed.

The invariants under test:

    * no request ever reaches UVLHub, and nothing here ever asks for or
      mentions a UVLHub key;
    * the stored marketplace token is the credential that is used;
    * metadata.toml stays byte-identical on every failure path, and only ever
      records what the MARKETPLACE reported.
"""

import json

import pytest
import requests as real_requests

import splent_cli.commands.spl.spl_publish as mod
import splent_cli.services.marketplace_api as api_mod
from splent_cli.commands.spl.spl_publish import spl_publish
from splent_cli.services import credentials
from splent_cli.services.marketplace_url import REGISTRY_URL_ENV

SPL = "sample_spl"
REGISTRY = "http://localhost:5818"
DOI = "10.5281/zenodo.4242"

METADATA_TEMPLATE = """\
# hand-written comment that must survive metadata edits
[spl]
name = "sample_spl"
description = "A sample SPL used in tests"

[spl.uvl]
mirror = "uvlhub.io"
doi = "{doi}"
file = "sample_spl.uvl"
"""

UVL_TEXT = (
    "features\n"
    "\tsample_spl\n"
    "\t\tmandatory\n"
    "\t\t\tauth {org 'splent-io', package 'splent_feature_auth'}\n"
)


def _make_spl(workspace, doi=""):
    """Create the working copy splent_spl_<name>/ with metadata and the model.

    The working copy is what an author edits, so it is what gets published and
    the only place a returned DOI is written back to.
    """
    spl_dir = workspace / "splent_spl_sample"
    spl_dir.mkdir(parents=True, exist_ok=True)
    (spl_dir / "metadata.toml").write_text(
        METADATA_TEMPLATE.format(doi=doi), encoding="utf-8"
    )
    (spl_dir / f"{SPL}.uvl").write_text(UVL_TEXT, encoding="utf-8")
    return spl_dir


def _all_output(result) -> str:
    return result.output + (result.stderr or "")


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data)
        else:
            self.text = ""

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json


class FakeNet:
    """Stands in for the ``requests`` module inside the marketplace client.

    Any request that matches no route is an unexpected network access and
    fails the test loudly, which is also how "did anything reach UVLHub" is
    answered: nothing routes there, so anything that tried would blow up here.
    """

    RequestException = real_requests.RequestException
    Timeout = real_requests.Timeout

    def __init__(self):
        self.calls = []  # (method, url, kwargs)
        self.routes = []  # (method, substring, response_or_exception)

    def route(self, method, substring, response):
        self.routes.append((method, substring, response))

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, sub, resp in self.routes:
            if m == method and sub in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"Unexpected network access: {method} {url}")


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    """No real key, no real registry, no real credential store."""
    monkeypatch.delenv("UVLHUB_API_KEY", raising=False)
    monkeypatch.delenv("UVLHUB_URL", raising=False)
    monkeypatch.delenv(credentials.TOKEN_ENV, raising=False)
    monkeypatch.setenv(REGISTRY_URL_ENV, REGISTRY)
    monkeypatch.setenv(
        credentials.CREDENTIALS_ENV, str(tmp_path / ".splent" / "credentials.json")
    )


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(api_mod, "requests", fake)
    return fake


@pytest.fixture
def logged_in():
    credentials.save(REGISTRY, token="splent_testtoken", identity="dev@example.com")


def _release_body(doi=DOI, **extra):
    body = {
        "name": SPL,
        "version": 1,
        "doi": doi,
        "uvl": {"mirror": "uvlhub.io", "doi": doi, "file": f"{SPL}.uvl"},
        "state": "published",
        "verification": "match",
        "idempotent": False,
    }
    body.update(extra)
    return body


def _release_response(doi=DOI, **extra):
    return FakeResponse(201, _release_body(doi, **extra))


# ===========================================================================
# The model: no UVLHub key, ever
# ===========================================================================


class TestTheModel:
    def test_no_uvlhub_key_is_needed_or_mentioned(
        self, workspace, runner, net, logged_in
    ):
        """The whole point of the relay, in one assertion.

        A developer following the old guidance obtained a personal UVLHub key
        and published directly, which is the exact flow the marketplace was
        built to replace. They are not told UVLHub exists, so the word does not
        appear: not in a prompt, not in a hint, not in the wording of the
        verification result.
        """
        _make_spl(workspace)
        net.route("POST", f"/api/v1/spls/{SPL}/releases", _release_response())

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        # The temporary directory pytest built is named after this test, so the
        # paths the command echoes carry the word by accident. Only what the
        # command actually wrote is under test.
        out = "\n".join(
            line
            for line in _all_output(result).splitlines()
            if str(workspace) not in line
        )
        assert "UVLHUB_API_KEY" not in out
        assert "developer/api-keys" not in out
        assert "uvlhub" not in out.lower()

    def test_the_help_text_names_login_and_not_uvlhub(self):
        assert "UVLHUB_API_KEY" not in (spl_publish.__doc__ or "")
        assert "splent login" in (spl_publish.__doc__ or "")

    def test_the_only_request_is_to_the_marketplace_release_endpoint(
        self, workspace, runner, net, logged_in
    ):
        _make_spl(workspace)
        net.route("POST", f"/api/v1/spls/{SPL}/releases", _release_response())

        runner.invoke(spl_publish, [SPL])

        assert len(net.calls) == 1
        method, url, kwargs = net.calls[0]
        assert method == "POST"
        assert url == f"{REGISTRY}/api/v1/spls/{SPL}/releases"
        assert kwargs["headers"]["Authorization"] == "Bearer splent_testtoken"
        assert kwargs["files"]["file"][0] == f"{SPL}.uvl"
        assert kwargs["files"]["file"][1] == UVL_TEXT.encode("utf-8")


# ===========================================================================
# Preconditions — clean, actionable errors before any network access
# ===========================================================================


class TestPreconditions:
    def test_not_logged_in_is_a_clean_error_with_no_network(
        self, workspace, runner, net
    ):
        spl_dir = _make_spl(workspace)
        before = (spl_dir / "metadata.toml").read_bytes()

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "splent login" in out
        assert "Traceback" not in out
        assert net.calls == []
        assert (spl_dir / "metadata.toml").read_bytes() == before

    def test_missing_local_uvl_names_every_place_it_looked(
        self, workspace, runner, net, logged_in
    ):
        spl_dir = _make_spl(workspace)
        (spl_dir / f"{SPL}.uvl").unlink()

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "splent_spl_sample" in out
        assert ".splent_cache" in out
        assert "spl:fetch" in out
        assert net.calls == []

    def test_a_model_in_the_cache_is_published_without_a_working_copy(
        self, workspace, runner, net, logged_in
    ):
        """A machine that only consumed the model can still push a fix."""
        cached = workspace / ".splent_cache" / "spls" / SPL
        cached.mkdir(parents=True)
        (cached / f"{SPL}.uvl").write_text(UVL_TEXT, encoding="utf-8")
        net.route("POST", f"/api/v1/spls/{SPL}/releases", _release_response())

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        assert len(net.calls) == 1

    def test_a_cache_only_publish_says_the_doi_was_not_recorded_anywhere(
        self, workspace, runner, net, logged_in
    ):
        """The cache is derived data, so a DOI written there would be lost."""
        cached = workspace / ".splent_cache" / "spls" / SPL
        cached.mkdir(parents=True)
        (cached / f"{SPL}.uvl").write_text(UVL_TEXT, encoding="utf-8")
        net.route("POST", f"/api/v1/spls/{SPL}/releases", _release_response())

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        assert "Nothing local records this DOI" in _all_output(result)
        # Nothing was written into the cache directory.
        assert not (cached / "metadata.toml").exists()


# ===========================================================================
# Server-side failures — metadata must stay byte-identical
# ===========================================================================


class TestServerFailures:
    def test_a_rejected_model_shows_the_marketplace_message(
        self, workspace, runner, net, logged_in
    ):
        spl_dir = _make_spl(workspace)
        before = (spl_dir / "metadata.toml").read_bytes()
        net.route(
            "POST",
            "/releases",
            FakeResponse(
                400,
                {"error": "The model does not parse. line 3", "code": "invalid_uvl"},
            ),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        assert "does not parse" in _all_output(result)
        assert (spl_dir / "metadata.toml").read_bytes() == before

    def test_a_quota_refusal_is_reported_as_throttling_not_as_a_bad_token(
        self, workspace, runner, net, logged_in
    ):
        _make_spl(workspace)
        net.route(
            "POST",
            "/releases",
            FakeResponse(
                429,
                {"error": "Try later.", "code": "quota_exceeded"},
                headers={"Retry-After": "3600"},
            ),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        assert "rate limiting" in _all_output(result)
        # A 429 says nothing about the token, so the credential survives.
        assert credentials.get(REGISTRY) is not None

    def test_a_dead_token_is_dropped_from_the_store(
        self, workspace, runner, net, logged_in
    ):
        """The marketplace answers 403 for a revoked token, not 401."""
        _make_spl(workspace)
        net.route(
            "POST",
            "/releases",
            FakeResponse(
                403,
                {"error": "This API token has been revoked", "code": "token_revoked"},
            ),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "revoked" in out
        assert "missing scope" not in out
        assert credentials.get(REGISTRY) is None

    def test_an_undetermined_release_is_never_presented_as_retryable(
        self, workspace, runner, net, logged_in
    ):
        spl_dir = _make_spl(workspace)
        before = (spl_dir / "metadata.toml").read_bytes()
        net.route(
            "POST",
            "/releases",
            FakeResponse(
                502,
                {
                    "error": "The publication was sent but UVLHub did not report back",
                    "code": "release_undetermined",
                },
            ),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        assert (spl_dir / "metadata.toml").read_bytes() == before

    def test_an_unreachable_marketplace_leaves_metadata_alone(
        self, workspace, runner, net, logged_in
    ):
        spl_dir = _make_spl(workspace)
        before = (spl_dir / "metadata.toml").read_bytes()
        net.route(
            "POST", "/releases", real_requests.ConnectionError("Connection refused")
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        assert "Could not reach the marketplace" in _all_output(result)
        assert (spl_dir / "metadata.toml").read_bytes() == before

    def test_an_accepted_release_with_no_doi_is_refused(
        self, workspace, runner, net, logged_in
    ):
        spl_dir = _make_spl(workspace, doi="10.5281/zenodo.OLD")
        before = (spl_dir / "metadata.toml").read_bytes()
        net.route("POST", "/releases", FakeResponse(201, {"name": SPL, "version": 1}))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        assert (spl_dir / "metadata.toml").read_bytes() == before


# ===========================================================================
# Happy paths — only what the marketplace reported is written down
# ===========================================================================


class TestHappyPaths:
    def test_the_reported_doi_is_written_into_metadata(
        self, workspace, runner, net, logged_in
    ):
        spl_dir = _make_spl(workspace)
        net.route("POST", "/releases", _release_response())

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        written = (spl_dir / "metadata.toml").read_text(encoding="utf-8")
        assert f'doi = "{DOI}"' in written
        # The hand-written comment survives a metadata edit.
        assert "hand-written comment" in written

    def test_the_concept_doi_is_recorded_when_the_marketplace_reports_it(
        self, workspace, runner, net, logged_in
    ):
        """It never changes across versions, so it is what makes drift visible.

        Without it, a product pinned to v2 has no way to learn the line has
        moved to v5 short of a directory service, which is the dependency the
        catalog was removed to be rid of.
        """
        spl_dir = _make_spl(workspace)
        net.route(
            "POST",
            "/releases",
            _release_response(concept_doi="10.5281/zenodo.CONCEPT", version=4),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        written = (spl_dir / "metadata.toml").read_text(encoding="utf-8")
        assert 'concept_doi = "10.5281/zenodo.CONCEPT"' in written
        assert 'version = "4"' in written
        assert "Concept DOI" in _all_output(result)

    def test_a_silent_server_does_not_erase_a_recorded_concept_doi(
        self, workspace, runner, net, logged_in
    ):
        """An answer that says nothing must not be written down as nothing."""
        spl_dir = _make_spl(workspace)
        meta = spl_dir / "metadata.toml"
        meta.write_text(
            meta.read_text(encoding="utf-8") + 'concept_doi = "10.5281/zenodo.KEEP"\n',
            encoding="utf-8",
        )
        net.route("POST", "/releases", _release_response())

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        assert 'concept_doi = "10.5281/zenodo.KEEP"' in meta.read_text(encoding="utf-8")

    def test_products_still_on_the_old_model_are_named(
        self, workspace, runner, net, logged_in
    ):
        """Minting a DOI moves no product onto it, and saying so is the point."""
        _make_spl(workspace)
        product = workspace / "demo_app"
        product.mkdir()
        (product / "pyproject.toml").write_text(
            '[project]\nname = "demo_app"\n\n'
            f'[tool.splent]\nspl = "{SPL}"\n\n'
            "[tool.splent.spl_model]\n"
            'doi = "10.5281/zenodo.OLD"\n'
            'version = "v1"\n'
        )
        net.route("POST", "/releases", _release_response())

        result = runner.invoke(spl_publish, [SPL])

        out = _all_output(result)
        assert result.exit_code == 0, out
        assert "demo_app" in out
        assert "spl:pin" in out

    def test_the_file_name_written_down_is_the_one_the_server_reported(
        self, workspace, runner, net, logged_in
    ):
        """UVLHub may rename an upload, and the marketplace reports the truth."""
        spl_dir = _make_spl(workspace)
        body = _release_body()
        body["uvl"]["file"] = "sample_spl_1.uvl"
        net.route("POST", "/releases", FakeResponse(201, body))

        runner.invoke(spl_publish, [SPL])

        assert 'file = "sample_spl_1.uvl"' in (spl_dir / "metadata.toml").read_text(
            encoding="utf-8"
        )

    def test_an_idempotent_answer_says_nothing_was_sent_on(
        self, workspace, runner, net, logged_in
    ):
        _make_spl(workspace)
        net.route("POST", "/releases", _release_response(idempotent=True))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        assert "already published" in _all_output(result)

    def test_a_mismatch_reported_by_the_marketplace_is_shown(
        self, workspace, runner, net, logged_in
    ):
        _make_spl(workspace)
        net.route("POST", "/releases", _release_response(verification="mismatch"))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        assert "DOES NOT match" in _all_output(result)

    def test_the_local_description_travels_with_the_model(
        self, workspace, runner, net, logged_in
    ):
        _make_spl(workspace)
        net.route("POST", "/releases", _release_response())

        runner.invoke(spl_publish, [SPL])

        _method, _url, kwargs = net.calls[0]
        assert kwargs["data"] == {"description": "A sample SPL used in tests"}


# ===========================================================================
# --dry-run — no network, no disk
# ===========================================================================


class TestDryRun:
    def test_dry_run_touches_neither_network_nor_disk(
        self, workspace, runner, net, logged_in
    ):
        spl_dir = _make_spl(workspace)
        before = (spl_dir / "metadata.toml").read_bytes()

        result = runner.invoke(spl_publish, [SPL, "--dry-run"])

        assert result.exit_code == 0, _all_output(result)
        assert net.calls == []
        assert (spl_dir / "metadata.toml").read_bytes() == before
        assert "nothing was changed" in _all_output(result)

    def test_dry_run_says_so_when_there_is_no_token(self, workspace, runner, net):
        _make_spl(workspace)

        result = runner.invoke(spl_publish, [SPL, "--dry-run"])

        assert result.exit_code == 0, _all_output(result)
        assert "Not logged in" in _all_output(result)
        assert net.calls == []


# ===========================================================================
# metadata.toml editing — the helper, on its own
# ===========================================================================


class TestMetadataEditing:
    def test_a_missing_uvl_section_is_added(self, tmp_path):
        path = tmp_path / "metadata.toml"
        path.write_text('[spl]\nname = "x"\n', encoding="utf-8")

        mod._update_metadata_uvl(str(path), doi=DOI, file="x.uvl")

        written = path.read_text(encoding="utf-8")
        assert "[spl.uvl]" in written
        assert f'doi = "{DOI}"' in written

    def test_a_file_that_cannot_be_written_is_restored(self, tmp_path, monkeypatch):
        path = tmp_path / "metadata.toml"
        original = '[spl]\nname = "x"\n\n[spl.uvl]\ndoi = ""\nfile = ""\n'
        path.write_text(original, encoding="utf-8")

        calls = []

        def once_then_restore(target, text):
            calls.append(target)
            if len(calls) == 1:
                raise OSError("disk full")
            path.write_text(text, encoding="utf-8")

        monkeypatch.setattr(mod, "atomic_write", once_then_restore)
        with pytest.raises(OSError):
            mod._update_metadata_uvl(str(path), doi=DOI, file="x.uvl")

        assert path.read_text(encoding="utf-8") == original


class TestAPausedMarketplace:
    def test_a_disabled_relay_is_not_reported_as_a_broken_server(
        self, workspace, runner, net, logged_in
    ):
        """Publishing spends the one shared key, so it defaults to off.

        A 503 with no more said reads as "the server fell over, retry in a
        moment", which is the opposite of the truth: nothing is broken and
        retrying changes nothing until somebody arms it.
        """
        _make_spl(workspace)
        net.route(
            "POST",
            "/releases",
            FakeResponse(
                503,
                {
                    "error": "Publishing is paused on this marketplace right now.",
                    "code": "publishing_disabled",
                },
            ),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "not accepting publications" in out
        assert "retrying will not help" in out
        assert "retry in a moment" not in out
        # Nothing about the token is wrong, so it stays.
        assert credentials.get(REGISTRY) is not None
