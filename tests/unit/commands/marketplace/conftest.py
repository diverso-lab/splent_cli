"""Fixtures for the marketplace command tests.

Every test in this package runs against a throwaway credential store: the
autouse fixture redirects SPLENT_CREDENTIALS into tmp_path, so no test can
ever read, write or delete a developer's real token.
"""

import pytest

from splent_cli.services import credentials, marketplace_api
from splent_cli.services.marketplace_url import REGISTRY_URL_ENV

PROD = "https://marketplace.splent.io"
LOCAL = "http://localhost:5818"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Isolate the credential store and the marketplace environment."""
    path = tmp_path / ".splent" / "credentials.json"
    monkeypatch.setenv(credentials.CREDENTIALS_ENV, str(path))
    monkeypatch.delenv(credentials.TOKEN_ENV, raising=False)
    monkeypatch.delenv(REGISTRY_URL_ENV, raising=False)
    monkeypatch.setenv("WORKING_DIR", str(tmp_path))
    return path


@pytest.fixture
def api(monkeypatch):
    """Replace the client methods with recording fakes.

    Usage::

        api.whoami_result = {...}       # or api.whoami_error = MarketplaceError
        api.login_result = {...}
    """

    class _Api:
        def __init__(self):
            self.login_calls = []
            self.whoami_calls = []
            self.revoke_calls = []
            self.login_result = {
                "token": "tok-123",
                "identity": "dev@example.com",
                "token_name": "splent-cli@laptop",
                "scopes": ["spl:publish"],
                "expires_at": "2099-01-01T00:00:00+00:00",
            }
            self.whoami_result = {
                "identity": "dev@example.com",
                "token_name": "splent-cli@laptop",
                "scopes": ["spl:publish"],
                "expires_at": "2099-01-01T00:00:00+00:00",
            }
            self.login_error = None
            self.whoami_error = None
            self.revoke_error = None

    fake = _Api()

    def _login(self, email, password, name, ttl=marketplace_api.DEFAULT_TTL_DAYS):
        fake.login_calls.append(
            {
                "base_url": self.base_url,
                "email": email,
                "password": password,
                "name": name,
                "ttl": ttl,
            }
        )
        if fake.login_error:
            raise fake.login_error
        return dict(fake.login_result)

    def _whoami(self):
        fake.whoami_calls.append({"base_url": self.base_url, "token": self.token})
        if fake.whoami_error:
            raise fake.whoami_error
        return dict(fake.whoami_result)

    def _revoke(self):
        fake.revoke_calls.append({"base_url": self.base_url, "token": self.token})
        if fake.revoke_error:
            raise fake.revoke_error
        return True

    monkeypatch.setattr(marketplace_api.MarketplaceClient, "login", _login)
    monkeypatch.setattr(marketplace_api.MarketplaceClient, "whoami", _whoami)
    monkeypatch.setattr(marketplace_api.MarketplaceClient, "revoke_current", _revoke)
    return fake


def error(code, message="server said no", status=None):
    return marketplace_api.MarketplaceError(message, status=status, code=code)
