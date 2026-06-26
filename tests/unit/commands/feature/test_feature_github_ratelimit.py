"""GitHub 403 rate-limit handling for feature:upgrade / feature:versions /
feature:search.

Hardened behavior under test: when the GitHub API answers with HTTP 403
(typically rate-limiting an unauthenticated request), each command must:

  * surface a CLEAN warning/error (no raw urllib HTTPError traceback), and
  * mention GITHUB_TOKEN so the user knows how to raise the limit,

instead of letting the HTTPError bubble up as an uncaught exception.

All network is mocked at the boundary (urllib.request.urlopen is patched per
module to raise a 403 HTTPError); no real network/git is touched.
"""

import io
import urllib.error

import pytest
from click.testing import CliRunner

from splent_cli.commands.feature.feature_upgrade import feature_upgrade
from splent_cli.commands.feature.feature_versions import feature_versions
from splent_cli.commands.feature.feature_search import feature_search


# ── Helpers ─────────────────────────────────────────────────────────────────


def _http_403(remaining=None):
    """Build a realistic urllib HTTPError for HTTP 403.

    `remaining` populates the X-RateLimit-Remaining header (a value of "0" is
    GitHub's signal that the request was rate-limited).
    """
    import email.message

    hdrs = email.message.Message()
    if remaining is not None:
        hdrs["X-RateLimit-Remaining"] = remaining
    return urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=403,
        msg="rate limit exceeded",
        hdrs=hdrs,
        fp=io.BytesIO(b'{"message": "API rate limit exceeded"}'),
    )


def _raise_403(remaining=None):
    def _fn(*args, **kwargs):
        raise _http_403(remaining=remaining)

    return _fn


def _no_traceback(text):
    assert "Traceback" not in text
    assert "HTTPError" not in text
    assert "urllib" not in text


def _write_product(tmp_path, monkeypatch, feature_line="splent_feature_auth@v1.0.0"):
    """Create a minimal product workspace with one declared feature and wire env."""
    product = "test_app"
    product_path = tmp_path / product
    product_path.mkdir(parents=True)
    (product_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "test_app"\n'
        'version = "0.1.0"\n\n'
        "[tool.splent]\n"
        f'features = ["{feature_line}"]\n'
    )
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    monkeypatch.setenv("SPLENT_APP", product)
    return product_path


# ── feature:upgrade ─────────────────────────────────────────────────────────


class TestUpgrade403:
    def test_403_is_clean_warning_mentions_token(self, tmp_path, monkeypatch):
        _write_product(tmp_path, monkeypatch)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_upgrade.urllib.request.urlopen",
                _raise_403(remaining="0"),
            )
            result = runner.invoke(feature_upgrade, [])

        # Skipped cleanly → no upgrades → exit 0, no crash.
        assert result.exit_code == 0
        assert result.exception is None or isinstance(result.exception, SystemExit)
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        assert "GITHUB_TOKEN" in result.output
        # The rate-limit skip warning is surfaced for the feature.
        assert "rate limit" in result.output.lower() or "403" in result.output

    def test_403_skips_feature_no_partial_upgrade(self, tmp_path, monkeypatch):
        # When the latest version cannot be resolved, the feature is skipped and
        # the command reports everything is up to date rather than upgrading.
        _write_product(tmp_path, monkeypatch)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_upgrade.urllib.request.urlopen",
                _raise_403(remaining="0"),
            )
            result = runner.invoke(feature_upgrade, ["splent_feature_auth"])

        assert result.exit_code == 0
        assert "already at the latest version" in result.output.lower()


# ── feature:versions ────────────────────────────────────────────────────────


class TestVersions403:
    def test_403_status_is_clean_and_mentions_token(self, tmp_path, monkeypatch):
        _write_product(tmp_path, monkeypatch)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_versions.urllib.request.urlopen",
                _raise_403(remaining="0"),
            )
            result = runner.invoke(
                feature_versions, ["splent_feature_auth", "--status"]
            )

        # 403 → _get_json returns None → gh_versions == [] → clean output.
        assert result.exit_code == 0
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        assert "GITHUB_TOKEN" in result.output
        # Rate-limit warning surfaced.
        assert "rate limit" in result.output.lower() or "403" in result.output

    def test_403_github_listing_is_clean(self, tmp_path, monkeypatch):
        _write_product(tmp_path, monkeypatch)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_versions.urllib.request.urlopen",
                _raise_403(remaining="0"),
            )
            result = runner.invoke(
                feature_versions, ["splent_feature_auth", "--github"]
            )

        assert result.exit_code == 0
        _no_traceback(result.output)
        # No tags resolved → reported gracefully, with the token hint.
        assert "no tags found" in result.output.lower()
        assert "GITHUB_TOKEN" in result.output

    def test_403_all_is_clean_and_mentions_token(self, tmp_path, monkeypatch):
        _write_product(tmp_path, monkeypatch)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_versions.urllib.request.urlopen",
                _raise_403(remaining="0"),
            )
            result = runner.invoke(feature_versions, ["--all"])

        assert result.exit_code == 0
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        assert "GITHUB_TOKEN" in result.output


# ── feature:search ──────────────────────────────────────────────────────────


class TestSearch403:
    def test_403_rate_limit_exceeded_is_clean_and_mentions_token(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_search.urllib.request.urlopen",
                _raise_403(remaining="0"),
            )
            result = runner.invoke(feature_search, [])

        # 403 rate-limit → clean SystemExit(1), token hint, no traceback.
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        assert "GITHUB_TOKEN" in result.output
        assert "rate limit" in result.output.lower()

    def test_403_forbidden_without_ratelimit_header_is_clean(
        self, tmp_path, monkeypatch
    ):
        # 403 with no X-RateLimit-Remaining header → "access forbidden" branch.
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_search.urllib.request.urlopen",
                _raise_403(remaining=None),
            )
            result = runner.invoke(feature_search, ["auth"])

        assert result.exit_code == 1
        _no_traceback(result.output)
        _no_traceback(result.stderr)
        assert "403" in result.output or "forbidden" in result.output.lower()
        assert "GITHUB_TOKEN" in result.output


# ── Core happy-path coverage (no rate limit) ────────────────────────────────


class TestHappyPath:
    def test_versions_latest_lists_github_and_pypi(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_versions._github_versions",
                lambda *a, **k: ["v2.0.0", "v1.0.0"],
            )
            mp.setattr(
                "splent_cli.commands.feature.feature_versions._pypi_versions",
                lambda *a, **k: ["2.0.0", "1.0.0"],
            )
            result = runner.invoke(
                feature_versions, ["splent_feature_auth", "--latest"]
            )

        assert result.exit_code == 0
        assert "v2.0.0" in result.output
        assert "2.0.0" in result.output
        _no_traceback(result.output)

    def test_search_lists_features(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        repos = [
            {"name": "splent_feature_auth", "description": "Auth feature"},
        ]

        def _fake_request(url, token):
            if "/orgs/" in url and "page=1" in url:
                return repos
            if "/orgs/" in url:
                return []
            # _latest_tag → releases/latest then tags
            if url.endswith("/releases/latest"):
                return {"tag_name": "v1.2.3"}
            return [{"name": "v1.2.3"}]

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_search._github_request",
                _fake_request,
            )
            result = runner.invoke(feature_search, [])

        assert result.exit_code == 0
        assert "splent_feature_auth" in result.output
        assert "v1.2.3" in result.output
        _no_traceback(result.output)

    def test_upgrade_all_up_to_date(self, tmp_path, monkeypatch):
        _write_product(tmp_path, monkeypatch)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        runner = CliRunner(mix_stderr=False)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "splent_cli.commands.feature.feature_upgrade._latest_remote_version",
                lambda *a, **k: "v1.0.0",
            )
            result = runner.invoke(feature_upgrade, [])

        assert result.exit_code == 0
        assert "already at the latest version" in result.output.lower()
        _no_traceback(result.output)
