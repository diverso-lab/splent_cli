"""Adversarial tests for spl:publish — publishing the local UVL to UVLHub.

The network is mocked at the boundary (the ``requests`` module inside
``spl_publish``), so no real HTTP ever happens. The invariant under test:

    metadata.toml must NEVER end up pointing at content that differs from
    the local UVL, and must stay byte-identical on every failure path.

Covers: missing API key (clean error + hint, zero network), server-side 400
on upload (server message surfaced, metadata intact), publish failing midway
(metadata intact), divergent verification (exit 1, metadata intact), the
happy paths for a new DOI and for a new version of an existing DOI, and
--dry-run touching neither network nor disk.
"""

import json

import pytest
import requests as real_requests

import splent_cli.commands.spl.spl_publish as mod
from splent_cli.commands.spl.spl_publish import spl_publish


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SPL = "sample_spl"

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
    "\tSamplePlatform\n"
    "\t\tmandatory\n"
    "\t\t\tauth {org 'splent-io', package 'splent_feature_auth'}\n"
)


def _make_spl(workspace, doi=""):
    """Create splent_catalog/<spl>/ with metadata.toml and the local UVL."""
    spl_dir = workspace / "splent_catalog" / SPL
    spl_dir.mkdir(parents=True, exist_ok=True)
    (spl_dir / "metadata.toml").write_text(
        METADATA_TEMPLATE.format(doi=doi), encoding="utf-8"
    )
    (spl_dir / f"{SPL}.uvl").write_text(UVL_TEXT, encoding="utf-8")
    return spl_dir


def _all_output(result) -> str:
    return result.output + (result.stderr or "")


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=None):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        if text is not None:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data)
        else:
            self.text = content.decode("utf-8", errors="replace")

    def json(self):
        if self._json is None:
            raise ValueError("no JSON body")
        return self._json


class FakeNet:
    """Stands in for the ``requests`` module inside spl_publish.

    Routes are (method, url-substring) → FakeResponse | Exception. Any
    request that matches no route is an unexpected network access and fails
    the test loudly.
    """

    RequestException = real_requests.RequestException

    def __init__(self):
        self.calls = []  # (method, url, kwargs)
        self.routes = []  # (method, substring, response_or_exception)

    def route(self, method, substring, response):
        self.routes.append((method, substring, response))

    def _dispatch(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, sub, resp in self.routes:
            if m == method and sub in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"Unexpected network access: {method} {url}")

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, **kwargs)


@pytest.fixture(autouse=True)
def _clean_uvlhub_env(monkeypatch):
    """Never leak a real key/URL from the developer's environment."""
    monkeypatch.delenv("UVLHUB_API_KEY", raising=False)
    monkeypatch.delenv("UVLHUB_URL", raising=False)


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(mod, "requests", fake)
    return fake


# ===========================================================================
# Preconditions — clean, actionable errors before any network access
# ===========================================================================


class TestPreconditions:
    def test_missing_api_key_clean_error_with_hint_no_network(
        self, workspace, runner, net
    ):
        """Without UVLHUB_API_KEY: exit 1, a hint pointing at
        /developer/api-keys, zero network calls, metadata untouched."""
        spl_dir = _make_spl(workspace)
        before = (spl_dir / "metadata.toml").read_bytes()

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "UVLHUB_API_KEY" in out
        assert "/developer/api-keys" in out
        assert "Traceback" not in out
        assert net.calls == []
        assert (spl_dir / "metadata.toml").read_bytes() == before

    def test_missing_metadata_clean_error(self, workspace, runner, net):
        """No splent_catalog/<spl>/metadata.toml → clean error, no network."""
        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        assert "Metadata not found" in _all_output(result)
        assert net.calls == []

    def test_missing_local_uvl_actionable_error(self, workspace, runner, net):
        """metadata.toml exists but the local .uvl does not → the error names
        the missing path and suggests spl:fetch; no network access."""
        spl_dir = _make_spl(workspace)
        (spl_dir / f"{SPL}.uvl").unlink()

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert f"{SPL}.uvl" in out
        assert "spl:fetch" in out
        assert net.calls == []


# ===========================================================================
# Server-side failures — metadata must stay byte-identical
# ===========================================================================


class TestServerFailures:
    def test_upload_400_shows_server_message_metadata_intact(
        self, workspace, runner, net, monkeypatch
    ):
        """An invalid UVL rejected by the server (400): the server's own
        message is visible to the user and metadata is untouched."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="")
        before = (spl_dir / "metadata.toml").read_bytes()

        net.route(
            "POST",
            "/api/v1/datasets/upload",
            FakeResponse(400, {"error": "UVL file does not parse: line 3"}),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "UVL file does not parse: line 3" in out
        assert "Traceback" not in out
        assert (spl_dir / "metadata.toml").read_bytes() == before
        # It stopped at the upload — publish was never attempted.
        assert len(net.calls) == 1

    def test_publish_fails_midway_metadata_intact(
        self, workspace, runner, net, monkeypatch
    ):
        """Upload succeeds but publish blows up (500): the error is surfaced
        and metadata is untouched — no half-published state recorded."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="")
        before = (spl_dir / "metadata.toml").read_bytes()

        net.route(
            "POST", "/api/v1/datasets/upload", FakeResponse(200, {"dataset_id": 7})
        )
        net.route(
            "POST",
            "/api/v1/datasets/7/publish",
            FakeResponse(500, {"error": "Zenodo deposition failed"}),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "Zenodo deposition failed" in out
        assert "Traceback" not in out
        assert (spl_dir / "metadata.toml").read_bytes() == before

    def test_network_error_during_upload_clean_message(
        self, workspace, runner, net, monkeypatch
    ):
        """A connection failure is reported cleanly, not as a traceback."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="")
        before = (spl_dir / "metadata.toml").read_bytes()

        net.route(
            "POST",
            "/api/v1/datasets/upload",
            real_requests.ConnectionError("connection refused"),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "Could not reach UVLHub" in out
        assert "Traceback" not in out
        assert (spl_dir / "metadata.toml").read_bytes() == before


# ===========================================================================
# Verification — never point metadata at content that differs from local
# ===========================================================================


class TestVerification:
    def test_divergent_content_exits_1_metadata_intact(
        self, workspace, runner, net, monkeypatch
    ):
        """Publish succeeds but the raw file on UVLHub differs from the local
        UVL byte-for-byte → red error, exit 1, metadata untouched."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="")
        before = (spl_dir / "metadata.toml").read_bytes()

        net.route(
            "POST", "/api/v1/datasets/upload", FakeResponse(200, {"dataset_id": 7})
        )
        net.route(
            "POST",
            "/api/v1/datasets/7/publish",
            FakeResponse(
                200,
                {
                    "doi": "10.5281/zenodo.99",
                    "deposition_id": 99,
                    "files": [{"name": "sample_spl.uvl", "raw_url": "x"}],
                },
            ),
        )
        net.route(
            "GET",
            "/files/raw/",
            FakeResponse(200, content=b"features\n\tSomethingElse\n"),
        )

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        out = _all_output(result)
        assert "differs" in out
        assert "NOT modified" in out
        assert (spl_dir / "metadata.toml").read_bytes() == before
        # No stray backup left behind either.
        assert not (spl_dir / "metadata.toml.bak").exists()

    def test_verification_http_error_exits_1_metadata_intact(
        self, workspace, runner, net, monkeypatch
    ):
        """The raw URL 404s after publishing → exit 1, metadata untouched."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="")
        before = (spl_dir / "metadata.toml").read_bytes()

        net.route(
            "POST", "/api/v1/datasets/upload", FakeResponse(200, {"dataset_id": 7})
        )
        net.route(
            "POST",
            "/api/v1/datasets/7/publish",
            FakeResponse(
                200,
                {
                    "doi": "10.5281/zenodo.99",
                    "files": [{"name": "sample_spl.uvl", "raw_url": "x"}],
                },
            ),
        )
        net.route("GET", "/files/raw/", FakeResponse(404, text="Not Found"))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 1
        assert "404" in _all_output(result)
        assert (spl_dir / "metadata.toml").read_bytes() == before


# ===========================================================================
# Happy paths
# ===========================================================================


class TestHappyPaths:
    def test_new_doi_uploads_publishes_verifies_and_writes_metadata(
        self, workspace, runner, net, monkeypatch
    ):
        """Empty doi → upload + publish; metadata gets the minted DOI and the
        REAL remote filename (renamed by the server), preserving the rest of
        the file (comments, description, mirror)."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="")
        local_bytes = (spl_dir / f"{SPL}.uvl").read_bytes()

        net.route(
            "POST", "/api/v1/datasets/upload", FakeResponse(200, {"dataset_id": 7})
        )
        net.route(
            "POST",
            "/api/v1/datasets/7/publish",
            FakeResponse(
                200,
                {
                    "doi": "10.5281/zenodo.99",
                    "deposition_id": 99,
                    # Server renamed the file (secure_filename / collision).
                    "files": [{"name": "sample_spl_1.uvl", "raw_url": "x"}],
                },
            ),
        )
        net.route("GET", "/files/raw/", FakeResponse(200, content=local_bytes))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        assert "10.5281/zenodo.99" in result.output

        # Upload request: API key header + title/description + uvl_file part.
        method, url, kwargs = net.calls[0]
        assert (method, "/api/v1/datasets/upload" in url) == ("POST", True)
        assert kwargs["headers"]["X-API-Key"] == "test-key"
        assert kwargs["data"]["title"] == "sample_spl"
        assert kwargs["data"]["description"] == "A sample SPL used in tests"
        assert "uvl_file" in kwargs["files"]

        # Verification hit the resolved raw URL with the RENAMED file.
        get_calls = [c for c in net.calls if c[0] == "GET"]
        assert len(get_calls) == 1
        assert "10.5281/zenodo.99" in get_calls[0][1]
        assert "sample_spl_1.uvl" in get_calls[0][1]

        # metadata.toml: doi + real remote filename written, rest preserved.
        text = (spl_dir / "metadata.toml").read_text()
        assert 'doi = "10.5281/zenodo.99"' in text
        assert 'file = "sample_spl_1.uvl"' in text
        assert "# hand-written comment that must survive metadata edits" in text
        assert 'description = "A sample SPL used in tests"' in text
        assert 'mirror = "uvlhub.io"' in text
        assert not (spl_dir / "metadata.toml.bak").exists()
        # The local UVL itself is never touched.
        assert (spl_dir / f"{SPL}.uvl").read_bytes() == local_bytes

    def test_existing_doi_publishes_new_version_and_updates_doi(
        self, workspace, runner, net, monkeypatch
    ):
        """Existing doi → DOI lookup + new-version (no upload/publish); the
        new DOI replaces the old one in metadata.toml."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="10.5281/zenodo.10")
        local_bytes = (spl_dir / f"{SPL}.uvl").read_bytes()

        net.route(
            "GET",
            "/api/v1/datasets/doi/10.5281/zenodo.10",
            FakeResponse(
                200,
                {
                    "dataset_id": 5,
                    "doi": "10.5281/zenodo.10",
                    "title": "sample_spl",
                    "files": [{"name": "sample_spl.uvl"}],
                },
            ),
        )
        net.route(
            "POST",
            "/api/v1/datasets/5/new-version",
            FakeResponse(
                200,
                {
                    "doi": "10.5281/zenodo.11",
                    "version": 2,
                    "files": [{"name": "sample_spl.uvl", "raw_url": "x"}],
                },
            ),
        )
        net.route("GET", "/files/raw/", FakeResponse(200, content=local_bytes))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        assert "10.5281/zenodo.11" in result.output

        # The new-dataset endpoints were never touched.
        urls = [url for _, url, _ in net.calls]
        assert not any("/datasets/upload" in u for u in urls)
        assert not any("/publish" in u for u in urls)

        # Verification used the NEW doi.
        raw_calls = [u for u in urls if "/files/raw/" in u]
        assert len(raw_calls) == 1
        assert "10.5281/zenodo.11" in raw_calls[0]

        text = (spl_dir / "metadata.toml").read_text()
        assert 'doi = "10.5281/zenodo.11"' in text
        assert "10.5281/zenodo.10" not in text
        assert 'file = "sample_spl.uvl"' in text


# ===========================================================================
# Custom UVLHUB_URL — verification must target the configured instance
# ===========================================================================


class TestCustomUvlhubUrl:
    def test_verification_get_starts_exactly_with_configured_base(
        self, workspace, runner, net, monkeypatch
    ):
        """With UVLHUB_URL pointing at another instance (and no raw_url in
        the response), the verification GET is anchored on EXACTLY that base
        — never on production uvlhub.io. Otherwise a publish to a custom
        instance verifies against production, 404s, and every retry mints a
        duplicate dataset."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        monkeypatch.setenv("UVLHUB_URL", "https://staging.example")
        spl_dir = _make_spl(workspace, doi="")
        local_bytes = (spl_dir / f"{SPL}.uvl").read_bytes()

        net.route(
            "POST", "/api/v1/datasets/upload", FakeResponse(200, {"dataset_id": 7})
        )
        net.route(
            "POST",
            "/api/v1/datasets/7/publish",
            FakeResponse(
                200,
                {
                    "doi": "10.5281/zenodo.99",
                    # No raw_url — the constructed fallback URL is exercised.
                    "files": [{"name": "sample_spl.uvl"}],
                },
            ),
        )
        net.route("GET", "/files/raw/", FakeResponse(200, content=local_bytes))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        get_calls = [c for c in net.calls if c[0] == "GET"]
        assert len(get_calls) == 1
        # Full-prefix assertion — not a substring check.
        assert get_calls[0][1].startswith(
            "https://staging.example/doi/10.5281/zenodo.99/files/raw/"
        )
        assert "uvlhub.io" not in get_calls[0][1]
        # Every request (upload, publish, verify) hit the SAME instance.
        for _, url, _ in net.calls:
            assert url.startswith("https://staging.example/")
        # And the publish completed: metadata records the minted DOI.
        text = (spl_dir / "metadata.toml").read_text()
        assert 'doi = "10.5281/zenodo.99"' in text

    def test_server_reported_raw_url_is_preferred_for_verification(
        self, workspace, runner, net, monkeypatch
    ):
        """When the publish response carries an absolute raw_url (built by
        the server with the host that actually served the publish), the
        verification GET uses it verbatim — no constructed URL."""
        monkeypatch.setenv("UVLHUB_API_KEY", "test-key")
        spl_dir = _make_spl(workspace, doi="")
        local_bytes = (spl_dir / f"{SPL}.uvl").read_bytes()

        raw_url = "https://mirror.example/dataset/42/download/sample_spl.uvl"
        net.route(
            "POST", "/api/v1/datasets/upload", FakeResponse(200, {"dataset_id": 7})
        )
        net.route(
            "POST",
            "/api/v1/datasets/7/publish",
            FakeResponse(
                200,
                {
                    "doi": "10.5281/zenodo.99",
                    "files": [{"name": "sample_spl.uvl", "raw_url": raw_url}],
                },
            ),
        )
        net.route("GET", raw_url, FakeResponse(200, content=local_bytes))

        result = runner.invoke(spl_publish, [SPL])

        assert result.exit_code == 0, _all_output(result)
        get_calls = [c for c in net.calls if c[0] == "GET"]
        assert len(get_calls) == 1
        assert get_calls[0][1] == raw_url

        text = (spl_dir / "metadata.toml").read_text()
        assert 'doi = "10.5281/zenodo.99"' in text
        assert 'file = "sample_spl.uvl"' in text


# ===========================================================================
# --dry-run — reports the plan, touches nothing
# ===========================================================================


class TestDryRun:
    def test_dry_run_new_dataset_touches_nothing(self, workspace, runner, net):
        """--dry-run with an empty doi: reports the upload+publish plan,
        performs zero network calls, writes nothing, needs no API key."""
        spl_dir = _make_spl(workspace, doi="")
        before_meta = (spl_dir / "metadata.toml").read_bytes()
        before_uvl = (spl_dir / f"{SPL}.uvl").read_bytes()

        result = runner.invoke(spl_publish, [SPL, "--dry-run"])

        assert result.exit_code == 0, _all_output(result)
        assert "dry run" in result.output.lower()
        assert "/api/v1/datasets/upload" in result.output
        assert net.calls == []
        assert (spl_dir / "metadata.toml").read_bytes() == before_meta
        assert (spl_dir / f"{SPL}.uvl").read_bytes() == before_uvl

    def test_dry_run_existing_doi_reports_new_version_plan(
        self, workspace, runner, net
    ):
        """--dry-run with an existing doi reports the new-version plan."""
        spl_dir = _make_spl(workspace, doi="10.5281/zenodo.10")
        before_meta = (spl_dir / "metadata.toml").read_bytes()

        result = runner.invoke(spl_publish, [SPL, "--dry-run"])

        assert result.exit_code == 0, _all_output(result)
        assert "10.5281/zenodo.10" in result.output
        assert "new-version" in result.output
        assert net.calls == []
        assert (spl_dir / "metadata.toml").read_bytes() == before_meta
