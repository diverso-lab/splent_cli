"""Unit tests for services/credentials.py — the marketplace credential store.

The store never touches a real path here: SPLENT_CREDENTIALS is redirected to
tmp_path by an autouse fixture, which is also the behaviour the override
exists for.
"""

import json
import os
import stat

import pytest

from splent_cli.services import credentials


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Point the store at a throwaway file and clear the token env var."""
    path = tmp_path / "store" / "credentials.json"
    monkeypatch.setenv(credentials.CREDENTIALS_ENV, str(path))
    monkeypatch.delenv(credentials.TOKEN_ENV, raising=False)
    return path


PROD = "https://marketplace.splent.io"
LOCAL = "http://splent_marketplace_app_web:5000"


# ── Where the store lives ───────────────────────────────────────────────────


class TestStorePath:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        target = tmp_path / "elsewhere.json"
        monkeypatch.setenv(credentials.CREDENTIALS_ENV, str(target))
        assert credentials.store_path() == target
        assert credentials.store_origin() == "env"

    def test_defaults_to_workspace_not_home(self, tmp_path, monkeypatch):
        """The workspace is the only mount that survives a container rebuild."""
        monkeypatch.delenv(credentials.CREDENTIALS_ENV, raising=False)
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        assert credentials.store_path() == tmp_path / ".splent" / "credentials.json"
        assert credentials.store_origin() == "workspace"

    def test_falls_back_to_home_without_workspace(self, tmp_path, monkeypatch):
        monkeypatch.delenv(credentials.CREDENTIALS_ENV, raising=False)
        monkeypatch.setenv("WORKING_DIR", str(tmp_path / "missing"))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        assert credentials.store_origin() == "home"
        assert credentials.store_path().name == "credentials.json"

    def test_never_the_workspace_env_file(self, tmp_path, monkeypatch):
        """The workspace .env is off limits, the store must live elsewhere."""
        monkeypatch.delenv(credentials.CREDENTIALS_ENV, raising=False)
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        env_file = tmp_path / ".env"
        env_file.write_text("SPLENT_APP=test_app\n")

        credentials.save(PROD, token="tok")

        assert env_file.read_text() == "SPLENT_APP=test_app\n"
        assert credentials.store_path() != env_file

    def test_not_inside_the_regenerable_cache(self, tmp_path, monkeypatch):
        """Cache directories get wiped by cache commands, credentials must not."""
        monkeypatch.delenv(credentials.CREDENTIALS_ENV, raising=False)
        monkeypatch.setenv("WORKING_DIR", str(tmp_path))
        assert ".splent_cache" not in str(credentials.store_path())


# ── Saving ──────────────────────────────────────────────────────────────────


class TestSave:
    def test_creates_file_with_0600_in_0700_dir(self, store):
        credentials.save(PROD, token="tok", identity="dev@example.com")

        assert store.exists()
        assert stat.S_IMODE(store.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.parent.stat().st_mode) == 0o700

    def test_writes_a_gitignore_next_to_it(self, store):
        credentials.save(PROD, token="tok")
        assert (store.parent / ".gitignore").read_text() == "*\n"

    def test_round_trips_the_entry(self, store):
        credentials.save(
            PROD,
            token="tok",
            identity="dev@example.com",
            token_name="splent-cli@laptop",
            scopes=["spl:publish"],
            expires_at="2026-10-24T10:00:00+00:00",
        )
        entry = credentials.get(PROD)
        assert entry["token"] == "tok"
        assert entry["identity"] == "dev@example.com"
        assert entry["token_name"] == "splent-cli@laptop"
        assert entry["scopes"] == ["spl:publish"]
        assert entry["expires_at"] == "2026-10-24T10:00:00+00:00"
        assert entry["updated_at"]

    def test_registries_coexist(self, store):
        """Production and a local marketplace are independent entries."""
        credentials.save(PROD, token="prod-token")
        credentials.save(LOCAL, token="local-token")

        assert credentials.get(PROD)["token"] == "prod-token"
        assert credentials.get(LOCAL)["token"] == "local-token"
        assert credentials.registries() == sorted([PROD, LOCAL])

    def test_key_is_normalised(self, store):
        """A trailing slash must not create a second, invisible entry."""
        credentials.save("http://localhost:5818/", token="tok")
        assert credentials.get("http://localhost:5818")["token"] == "tok"
        assert credentials.registries() == ["http://localhost:5818"]

    def test_overwrites_same_registry(self, store):
        credentials.save(PROD, token="old")
        credentials.save(PROD, token="new")
        assert credentials.get(PROD)["token"] == "new"
        assert len(credentials.registries()) == 1

    def test_refuses_empty_token(self, store):
        with pytest.raises(credentials.CredentialsError):
            credentials.save(PROD, token="   ")

    def test_reports_unwritable_location_actionably(self, tmp_path, monkeypatch):
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        monkeypatch.setenv(
            credentials.CREDENTIALS_ENV, str(locked / "sub" / "credentials.json")
        )
        try:
            with pytest.raises(credentials.CredentialsError) as exc:
                credentials.save(PROD, token="tok")
        finally:
            locked.chmod(0o700)
        message = str(exc.value)
        assert "Could not write the credential store" in message
        assert credentials.CREDENTIALS_ENV in message


# ── Reading ─────────────────────────────────────────────────────────────────


class TestResolve:
    def test_none_when_nothing_stored(self, store):
        assert credentials.resolve(PROD) is None

    def test_reads_the_stored_token_and_hides_it_from_the_entry(self, store):
        credentials.save(PROD, token="tok", identity="dev@example.com")
        cred = credentials.resolve(PROD)

        assert cred.token == "tok"
        assert cred.source == "file"
        assert cred.source_label == str(store)
        assert cred.entry["identity"] == "dev@example.com"
        assert "token" not in cred.entry

    def test_environment_token_wins(self, store, monkeypatch):
        credentials.save(PROD, token="file-token")
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")

        cred = credentials.resolve(PROD)
        assert cred.token == "env-token"
        assert cred.from_environment
        assert cred.source_label == credentials.TOKEN_ENV

    def test_environment_token_borrows_no_cached_identity(self, store, monkeypatch):
        """The file describes another token, it must not answer for this one."""
        credentials.save(PROD, token="file-token", identity="someone@example.com")
        monkeypatch.setenv(credentials.TOKEN_ENV, "env-token")
        assert credentials.resolve(PROD).entry == {}

    def test_blank_environment_token_is_ignored(self, store, monkeypatch):
        credentials.save(PROD, token="file-token")
        monkeypatch.setenv(credentials.TOKEN_ENV, "   ")
        assert credentials.resolve(PROD).token == "file-token"

    def test_other_registry_is_not_reused(self, store):
        credentials.save(PROD, token="prod-token")
        assert credentials.resolve(LOCAL) is None

    def test_empty_file_is_not_an_error(self, store):
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("")
        assert credentials.resolve(PROD) is None

    def test_corrupt_store_says_what_to_do(self, store):
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{not json")
        with pytest.raises(credentials.CredentialsError) as exc:
            credentials.load_store()
        assert "splent login" in str(exc.value)

    def test_unexpected_layout_is_rejected(self, store):
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"registries": []}))
        with pytest.raises(credentials.CredentialsError):
            credentials.load_store()


# ── Deleting ────────────────────────────────────────────────────────────────


class TestDelete:
    def test_removes_only_that_registry(self, store):
        credentials.save(PROD, token="prod-token")
        credentials.save(LOCAL, token="local-token")

        assert credentials.delete(PROD) is True
        assert credentials.get(PROD) is None
        assert credentials.get(LOCAL)["token"] == "local-token"

    def test_false_when_nothing_stored(self, store):
        assert credentials.delete(PROD) is False

    def test_keeps_file_mode_after_delete(self, store):
        credentials.save(PROD, token="tok")
        credentials.save(LOCAL, token="tok2")
        credentials.delete(PROD)
        assert stat.S_IMODE(store.stat().st_mode) == 0o600

    def test_corrupt_store_is_reset_rather_than_blocking_logout(self, store):
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{not json")
        assert credentials.delete(PROD) is True
        assert credentials.load_store()["registries"] == {}


class TestTokenIsNeverRendered:
    """ "Never logged" has to survive a careless print or a traceback."""

    def test_the_token_is_kept_out_of_the_repr(self):
        cred = credentials.Credential(
            token="super-secret-token",
            source="file",
            source_label="/workspace/.splent/credentials.json",
        )
        assert "super-secret-token" not in repr(cred)
        assert "super-secret-token" not in str(cred)

    def test_the_provenance_is_still_visible(self):
        """Hiding the secret must not hide the fields whoami explains itself with."""
        cred = credentials.Credential(
            token="t", source="environment", source_label="SPLENT_MARKETPLACE_TOKEN"
        )
        assert "environment" in repr(cred)
        assert "SPLENT_MARKETPLACE_TOKEN" in repr(cred)

    def test_the_token_is_still_readable_by_the_client(self):
        cred = credentials.Credential(token="t", source="file", source_label="p")
        assert cred.token == "t"

    def test_a_resolved_credential_does_not_render_its_token(self, store):
        credentials.save("https://marketplace.splent.io", token="super-secret-token")
        cred = credentials.resolve("https://marketplace.splent.io")
        assert "super-secret-token" not in repr(cred)
        assert cred.token == "super-secret-token"
