"""Single place that resolves the SPLENT marketplace registry URL.

Every command that talks to the marketplace (login, logout, whoami and, later,
publishing) must agree on WHICH marketplace it is talking to, and must spell it
the same way, because the credential store is keyed by that string. Two
spellings of the same registry ("https://marketplace.splent.io" and
"https://marketplace.splent.io/") would mean two entries, one of which is
invisible to the developer.

Precedence, highest first:

  1. the explicit ``--registry URL`` option of the command
  2. the ``SPLENT_MARKETPLACE_URL`` environment variable
  3. the production default, https://marketplace.splent.io

The same developer legitimately has several URLs for the same product: from
inside ``splent_cli_container`` the marketplace answers at
``http://splent_marketplace_app_web:5000`` (both containers share
``splent_network``), while from the host it is ``http://localhost:5818``.
Those are different registries as far as the credential store is concerned,
which is exactly why the store is keyed by the normalised URL.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

DEFAULT_REGISTRY_URL = "https://marketplace.splent.io"
REGISTRY_URL_ENV = "SPLENT_MARKETPLACE_URL"

# Documented development URLs, quoted verbatim in error messages so a
# developer who typed the wrong one gets the right one back.
LOCAL_CONTAINER_URL = "http://splent_marketplace_app_web:5000"
LOCAL_HOST_URL = "http://localhost:5818"

_DEFAULT_PORTS = {"http": "80", "https": "443"}

# Hosts that are never reachable over TLS, used only to infer a scheme when
# the developer typed the URL without one.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


class InvalidRegistryURL(ValueError):
    """The configured registry URL is not usable as an HTTP endpoint."""


@dataclass(frozen=True)
class RegistryTarget:
    """The resolved registry plus where the value came from.

    ``origin`` is one of ``option``, ``env`` or ``default``. ``whoami`` prints
    it, because "which marketplace am I actually talking to and who decided
    that" is the first question when CI and a laptop disagree.
    """

    url: str
    origin: str

    @property
    def origin_label(self) -> str:
        if self.origin == "option":
            return "--registry"
        if self.origin == "env":
            return REGISTRY_URL_ENV
        return "default"


def _infer_scheme(raw: str) -> str:
    """Pick http/https for a URL typed without a scheme.

    A bare public domain gets https. Local development targets (``localhost``,
    an IP, or a bare Docker service name such as
    ``splent_marketplace_app_web:5000``, which never has a dot) get http,
    because those never terminate TLS.
    """
    host = raw.split("/", 1)[0]
    hostname = host.rsplit(":", 1)[0] if ":" in host else host
    if hostname.lower() in _LOCAL_HOSTS or "." not in hostname:
        return "http"
    return "https"


def normalize_registry_url(url: str) -> str:
    """Return the canonical spelling of *url*, or raise InvalidRegistryURL.

    Canonical means one registry maps to exactly one string, so it can be used
    as a credential-store key: lowercase scheme and host, no default port, no
    trailing slash, no query and no fragment. The path is preserved (a
    marketplace may be mounted under a prefix) minus its trailing slash.
    """
    if url is None:
        raise InvalidRegistryURL("The marketplace URL is empty.")

    raw = str(url).strip()
    if not raw:
        raise InvalidRegistryURL("The marketplace URL is empty.")
    if any(c.isspace() for c in raw):
        raise InvalidRegistryURL(f"The marketplace URL contains whitespace ({raw!r}).")

    if "://" not in raw:
        raw = f"{_infer_scheme(raw)}://{raw}"

    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidRegistryURL(
            f"The marketplace URL must use http or https, got {scheme!r} in {url!r}."
        )
    if not parts.hostname:
        raise InvalidRegistryURL(f"The marketplace URL has no host ({url!r}).")

    host = parts.hostname.lower()
    if ":" in host:  # IPv6 literal, urlsplit strips the brackets
        host = f"[{host}]"
    netloc = host
    port = parts.port
    if port is not None and str(port) != _DEFAULT_PORTS[scheme]:
        netloc = f"{host}:{port}"
    if parts.username:
        # Credentials in the URL would end up in the store key and in output.
        raise InvalidRegistryURL(
            "The marketplace URL must not carry a user or password. "
            "Authenticate with splent login instead."
        )

    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def resolve_registry(override: str | None = None) -> RegistryTarget:
    """Resolve the registry to talk to, with its origin, already normalised."""
    if override is not None and str(override).strip():
        return RegistryTarget(normalize_registry_url(override), "option")

    env_value = os.getenv(REGISTRY_URL_ENV)
    if env_value is not None and env_value.strip():
        try:
            return RegistryTarget(normalize_registry_url(env_value), "env")
        except InvalidRegistryURL as e:
            raise InvalidRegistryURL(f"{e} Fix {REGISTRY_URL_ENV} or unset it.")

    return RegistryTarget(normalize_registry_url(DEFAULT_REGISTRY_URL), "default")


def resolve_registry_url(override: str | None = None) -> str:
    """Convenience wrapper for callers that only need the URL string."""
    return resolve_registry(override).url
