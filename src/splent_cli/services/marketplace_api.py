"""Single boundary for every call to the SPLENT marketplace backend.

This module is to the marketplace what :mod:`splent_cli.services.registry` is
to GitHub and PyPI: the ONLY place that knows the wire format, the only place
that builds URLs and headers, and the only place that turns an HTTP response
into a typed error. Commands never touch ``requests`` and never see a status
code; they catch :class:`MarketplaceError`, read ``.code`` and print something
actionable.

The server contract (being built in parallel, so parsing stays tolerant of
extra fields and loud about missing ones)::

    POST   /api/v1/auth/tokens          {email, password, name, ttl_days}
                                        -> {token, expires_at, scopes, ...}
    GET    /api/v1/auth/whoami          Bearer -> identity, scopes, name, expiry
    DELETE /api/v1/auth/tokens/current  Bearer
    GET    /api/v1/spls                 public
    GET    /api/v1/spls/<name>          public -> {name, description, uvl{...}}
    POST   /api/v1/spls/<name>/releases Bearer, multipart with the uvl file

Nothing here prints, and nothing here ever puts a token in a message: an error
string from this module is safe to show to the user verbatim.
"""

from __future__ import annotations

import re

import requests

from splent_cli.services.marketplace_url import normalize_registry_url

USER_AGENT = "splent-cli"

DEFAULT_TIMEOUT = 15
UPLOAD_TIMEOUT = 60

#: Lifetime requested for a token created by ``splent login``.
DEFAULT_TTL_DAYS = 90

#: Multipart field name carrying the UVL model on a release upload.
UVL_FIELD_NAME = "file"

# ── Error codes ───────────────────────────────────────────────────────
# Stable identifiers commands switch on. The server may send its own
# ``code``; known values are mapped onto these, unknown ones fall back to
# the HTTP status.

CODE_UNREACHABLE = "unreachable"
CODE_INVALID_CREDENTIALS = "invalid_credentials"
CODE_UNAUTHENTICATED = "unauthenticated"
CODE_TOKEN_EXPIRED = "token_expired"
CODE_TOKEN_REVOKED = "token_revoked"
CODE_ACCOUNT_INACTIVE = "account_inactive"
CODE_FORBIDDEN = "forbidden"
CODE_NOT_FOUND = "not_found"
CODE_INVALID_REQUEST = "invalid_request"
CODE_CONFLICT = "conflict"
CODE_RATE_LIMITED = "rate_limited"
CODE_PUBLISHING_DISABLED = "publishing_disabled"
CODE_SERVER_ERROR = "server_error"
CODE_BAD_RESPONSE = "bad_response"
CODE_UNEXPECTED = "unexpected"

_SERVER_CODE_ALIASES = {
    "invalid_credentials": CODE_INVALID_CREDENTIALS,
    "bad_credentials": CODE_INVALID_CREDENTIALS,
    "invalid_login": CODE_INVALID_CREDENTIALS,
    "wrong_password": CODE_INVALID_CREDENTIALS,
    "inactive": CODE_ACCOUNT_INACTIVE,
    "inactive_account": CODE_ACCOUNT_INACTIVE,
    "account_inactive": CODE_ACCOUNT_INACTIVE,
    "user_inactive": CODE_ACCOUNT_INACTIVE,
    "not_activated": CODE_ACCOUNT_INACTIVE,
    "pending_activation": CODE_ACCOUNT_INACTIVE,
    "expired": CODE_TOKEN_EXPIRED,
    "token_expired": CODE_TOKEN_EXPIRED,
    "expired_token": CODE_TOKEN_EXPIRED,
    "revoked": CODE_TOKEN_REVOKED,
    "token_revoked": CODE_TOKEN_REVOKED,
    "unauthenticated": CODE_UNAUTHENTICATED,
    "unauthorized": CODE_UNAUTHENTICATED,
    "invalid_token": CODE_UNAUTHENTICATED,
    "forbidden": CODE_FORBIDDEN,
    "insufficient_scope": CODE_FORBIDDEN,
    "not_found": CODE_NOT_FOUND,
    "unknown_spl": CODE_NOT_FOUND,
    "conflict": CODE_CONFLICT,
    "already_exists": CODE_CONFLICT,
    "release_in_flight": CODE_CONFLICT,
    "release_undetermined": CODE_CONFLICT,
    "rate_limited": CODE_RATE_LIMITED,
    "ratelimit": CODE_RATE_LIMITED,
    "rate_limit_exceeded": CODE_RATE_LIMITED,
    "too_many_requests": CODE_RATE_LIMITED,
    "throttled": CODE_RATE_LIMITED,
    "quota_exceeded": CODE_RATE_LIMITED,
    "invalid_request": CODE_INVALID_REQUEST,
    "publishing_disabled": CODE_PUBLISHING_DISABLED,
}

# Fallbacks for a server that sends no ``code`` at all. They match ENGLISH
# prose, so they are a last resort and never the primary path: the marketplace
# negotiates Accept-Language on its API routes, which means the same refusal
# arrives in Spanish for a Spanish client and every one of these stops matching.
# The codes above are the contract; these only keep an older server usable.
_INACTIVE_HINTS = (
    "inactive",
    "not active",
    "not activated",
    "pending activation",
    "awaiting activation",
    "activated by an admin",
)

_EXPIRED_HINTS = ("expired",)
_REVOKED_HINTS = ("revoked",)
_UNKNOWN_TOKEN_HINTS = ("invalid api token", "invalid token", "unknown token")

#: Codes that mean "the credential you sent is dead". Commands delete the
#: stored entry when they see one, so the next command says "not logged in"
#: instead of repeating the same failure for ever. An inactive ACCOUNT is
#: deliberately not in here: the token is fine, the account is not activated.
#: Neither is CODE_RATE_LIMITED, and that one matters: a 429 says nothing at
#: all about the token, so treating it as dead would throw away a perfectly
#: good credential just because the developer ran a command too often.
DEAD_TOKEN_CODES = frozenset(
    {CODE_UNAUTHENTICATED, CODE_TOKEN_EXPIRED, CODE_TOKEN_REVOKED}
)


class MarketplaceError(Exception):
    """A marketplace request failed.

    Carries the HTTP ``status`` (None when the server was never reached) and a
    stable ``code`` from the constants above, so the command layer can map the
    failure to an actionable message without re-parsing anything.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        code: str | None = None,
        *,
        reason: str | None = None,
        retry_after: str | None = None,
        server_message: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code or CODE_UNEXPECTED
        #: What the SERVER actually said, empty when this message was
        #: synthesized here. Commands quote only this, so they never echo our
        #: own wording back as if the marketplace had produced it.
        self.server_message = (server_message or "").strip()
        #: Short cause ("Connection refused"), when there is one worth showing
        #: on its own line. Keeps commands from reprinting the whole message.
        self.reason = reason
        #: The ``Retry-After`` header, verbatim, when the server sent one.
        self.retry_after = retry_after

    @property
    def unreachable(self) -> bool:
        return self.code == CODE_UNREACHABLE

    @property
    def rate_limited(self) -> bool:
        """The registry is throttling us, which is not a credential problem.

        Mirrors ``RegistryError.rate_limited`` on the GitHub/PyPI boundary, so
        both registries answer the same question the same way.
        """
        return self.code == CODE_RATE_LIMITED

    @property
    def dead_token(self) -> bool:
        """True when the credential used should be dropped from the store."""
        return self.code in DEAD_TOKEN_CODES


# ── Response helpers ──────────────────────────────────────────────────


def _transport_reason(error: Exception) -> str:
    """Condense a requests/urllib3 exception into one readable cause.

    ``requests`` wraps the OS error in several layers of retry machinery, and
    dumping all of it at a developer hides the one useful phrase
    ("Connection refused", "Name or service not known").
    """
    text = str(error).strip() or error.__class__.__name__
    errno_match = re.search(r"\[Errno -?\d+\]\s*([^'\"()\]]+)", text)
    if errno_match:
        return errno_match.group(1).strip().rstrip(".")
    for phrase in (
        "Name or service not known",
        "Temporary failure in name resolution",
        "nodename nor servname provided",
        "Connection refused",
        "Connection reset by peer",
        "certificate verify failed",
        "timed out",
    ):
        if phrase.lower() in text.lower():
            return phrase
    return text[:200]


def _retry_after(response) -> str | None:
    """The ``Retry-After`` header, when the server sent a usable one.

    Tolerant of responses that carry no headers at all (test stand-ins, and
    proxies that throttle without saying for how long), mirroring how the
    GitHub/PyPI boundary reads its own rate-limit headers.
    """
    headers = getattr(response, "headers", None)
    if not hasattr(headers, "get"):
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload(response) -> object:
    """Parsed JSON body, or None when the body is empty or not JSON."""
    if response is None:
        return None
    text = (getattr(response, "text", "") or "").strip()
    if not text:
        return None
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - any body that is not JSON
        return None


def _looks_like_markup(text: str) -> bool:
    """Whether a body is an HTML/XML document rather than a message."""
    return text.lstrip().startswith("<")


def _error_fields(payload: object, response) -> tuple[str, str | None]:
    """Extract ``(message, server_code)`` from an error body.

    Accepts the flat shape ``{"error": "...", "code": "..."}`` and the nested
    one ``{"error": {"message": "...", "code": "..."}}``, plus ``message`` and
    ``detail`` as aliases, because the contract may still shift.
    """
    message = ""
    code = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "detail", "description"):
                if error.get(key):
                    message = str(error[key])
                    break
            if error.get("code"):
                code = str(error["code"])
        elif isinstance(error, str) and error:
            message = error
        if not message:
            for key in ("message", "detail", "description", "msg"):
                if payload.get(key):
                    message = str(payload[key])
                    break
        if code is None and payload.get("code"):
            code = str(payload["code"])

    if not message:
        text = (getattr(response, "text", "") or "").strip()
        # A markup body is an error PAGE, not an error message: a URL that is
        # not a SPLENT API answers 404 with a whole HTML document, and pasting
        # 300 characters of it at the developer buries the actionable advice
        # under noise. A plain-text body is still worth showing.
        message = "" if _looks_like_markup(text) else text[:300]

    return message, code


def _classify(status: int, message: str, server_code: str | None) -> str:
    """Map an HTTP failure onto one of the CODE_* constants.

    A dead credential is 403 here, not 401. The marketplace reserves 401 for a
    request that carried no token at all and answers 403 for one that carried a
    token it will not accept, whether that token was revoked, has expired or was
    never issued. An earlier version read the expired/revoked wording only under
    401, so against this server every one of those collapsed to
    ``CODE_FORBIDDEN``: the credential was never dropped from the store and the
    developer was told to ask for a missing scope on a token the server had
    destroyed. The reason is read for both statuses now, and the status only
    decides the fallback when nothing else identifies the failure.
    """
    if server_code:
        mapped = _SERVER_CODE_ALIASES.get(server_code.strip().lower())
        if mapped:
            return mapped

    lowered = (message or "").lower()
    if status in (401, 403):
        if any(h in lowered for h in _INACTIVE_HINTS):
            return CODE_ACCOUNT_INACTIVE
        if any(h in lowered for h in _EXPIRED_HINTS):
            return CODE_TOKEN_EXPIRED
        if any(h in lowered for h in _REVOKED_HINTS):
            return CODE_TOKEN_REVOKED
        if any(h in lowered for h in _UNKNOWN_TOKEN_HINTS):
            return CODE_UNAUTHENTICATED
        # 401 means no usable credential was presented, so there is nothing to
        # keep. 403 with no recognisable reason is a permission problem on a
        # token that is otherwise fine, and throwing it away would be wrong.
        return CODE_UNAUTHENTICATED if status == 401 else CODE_FORBIDDEN
    if status == 404:
        return CODE_NOT_FOUND
    if status == 409:
        return CODE_CONFLICT
    if status == 429:
        return CODE_RATE_LIMITED
    if status in (400, 422):
        return CODE_INVALID_REQUEST
    if status >= 500:
        return CODE_SERVER_ERROR
    return CODE_UNEXPECTED


def _pick(payload: dict, *keys: str):
    """First present, non-empty value among *keys*."""
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _as_scopes(value) -> list[str]:
    if isinstance(value, str):
        return [s for s in value.replace(",", " ").split() if s]
    if isinstance(value, (list, tuple)):
        return [str(s) for s in value if s]
    return []


def parse_identity(payload: dict) -> dict:
    """Normalise a whoami / login body into the fields the CLI shows.

    Tolerant on purpose: the identity may be flat or nested under ``user`` /
    ``identity`` / ``account``, and the token metadata flat or nested under
    ``token``. Unknown extra fields are kept in ``raw``.
    """
    if not isinstance(payload, dict):
        return {
            "identity": None,
            "scopes": [],
            "token_name": None,
            "expires_at": None,
            "raw": {},
        }

    nested_user = {}
    for key in ("user", "identity", "account"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested_user = value
            break

    nested_token = payload.get("token")
    if not isinstance(nested_token, dict):
        nested_token = {}

    identity = _pick(payload, "identity", "email", "username", "login")
    if isinstance(identity, dict):
        identity = _pick(identity, "email", "identity", "username")
    if not identity and nested_user:
        identity = _pick(nested_user, "email", "identity", "username", "login")

    scopes = _as_scopes(
        _pick(payload, "scopes", "scope")
        or _pick(nested_token, "scopes", "scope")
        or []
    )

    token_name = _pick(payload, "token_name", "name", "label")
    if not token_name:
        token_name = _pick(nested_token, "name", "token_name", "label")

    expires_at = _pick(payload, "expires_at", "expiry", "expires", "expires_on")
    if not expires_at:
        expires_at = _pick(nested_token, "expires_at", "expiry", "expires")

    return {
        "identity": str(identity) if identity else None,
        "scopes": scopes,
        "token_name": str(token_name) if token_name else None,
        "expires_at": str(expires_at) if expires_at else None,
        "raw": payload,
    }


# ── Client ────────────────────────────────────────────────────────────


class MarketplaceClient:
    """Typed client for one registry. Instantiate per registry URL."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = normalize_registry_url(base_url)
        self.token = (token or "").strip() or None
        self.timeout = timeout

    # -- plumbing ------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _headers(self, *, auth: bool) -> dict:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if auth:
            if not self.token:
                raise MarketplaceError(
                    "No marketplace token available for this request.",
                    status=None,
                    code=CODE_UNAUTHENTICATED,
                )
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = False,
        json_body: dict | None = None,
        files: dict | None = None,
        data: dict | None = None,
        timeout: int | None = None,
        unauthenticated_code: str | None = None,
    ):
        """Perform the request. Returns parsed JSON, or None for an empty body.

        Raises :class:`MarketplaceError` for every non-2xx answer and for every
        transport failure, so callers only ever deal with one exception type.
        """
        url = self._url(path)
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(auth=auth),
                json=json_body,
                files=files,
                data=data,
                timeout=timeout or self.timeout,
            )
        except requests.Timeout:
            raise MarketplaceError(
                f"The marketplace at {self.base_url} did not answer in time.",
                status=None,
                code=CODE_UNREACHABLE,
                reason=f"no answer within {timeout or self.timeout} seconds",
            )
        except requests.RequestException as e:
            reason = _transport_reason(e)
            raise MarketplaceError(
                f"Could not reach the marketplace at {self.base_url} ({reason}).",
                status=None,
                code=CODE_UNREACHABLE,
                reason=reason,
            )

        status = getattr(response, "status_code", 0) or 0
        if 200 <= status < 300:
            return _payload(response)

        message, server_code = _error_fields(_payload(response), response)
        code = _classify(status, message, server_code)
        if code == CODE_UNAUTHENTICATED and unauthenticated_code:
            code = unauthenticated_code
        server_message = message
        if not message:
            message = f"The marketplace answered HTTP {status}."
        raise MarketplaceError(
            message,
            status=status,
            code=code,
            retry_after=_retry_after(response),
            server_message=server_message,
        )

    def _expect_dict(self, payload, what: str) -> dict:
        if not isinstance(payload, dict):
            raise MarketplaceError(
                f"The marketplace returned an unexpected {what} "
                "(a JSON object was expected).",
                status=None,
                code=CODE_BAD_RESPONSE,
            )
        return payload

    # -- authentication ------------------------------------------------

    def login(
        self,
        email: str,
        password: str,
        name: str,
        ttl: int = DEFAULT_TTL_DAYS,
    ) -> dict:
        """Create an API token. Returns the normalised identity plus ``token``.

        The password is only ever sent in the request body, never stored and
        never echoed back in an error message.
        """
        payload = self._request(
            "POST",
            "/api/v1/auth/tokens",
            json_body={
                "email": email,
                "password": password,
                "name": name,
                "ttl_days": ttl,
            },
            unauthenticated_code=CODE_INVALID_CREDENTIALS,
        )
        body = self._expect_dict(payload, "login response")
        token = _pick(body, "token", "access_token", "api_token")
        if not token or not isinstance(token, str):
            raise MarketplaceError(
                "The marketplace accepted the login but returned no token field.",
                status=None,
                code=CODE_BAD_RESPONSE,
            )
        result = parse_identity(body)
        result["token"] = token.strip()
        if not result.get("identity"):
            result["identity"] = email
        if not result.get("token_name"):
            result["token_name"] = name
        return result

    def whoami(self) -> dict:
        """Identity, scopes, token name and expiry behind the current token."""
        payload = self._request("GET", "/api/v1/auth/whoami", auth=True)
        body = self._expect_dict(payload, "whoami response")
        result = parse_identity(body)
        if not result["identity"]:
            raise MarketplaceError(
                "The marketplace whoami response did not include an identity.",
                status=None,
                code=CODE_BAD_RESPONSE,
            )
        return result

    def revoke_current(self) -> bool:
        """Revoke the token being used. True when the server confirmed it."""
        self._request("DELETE", "/api/v1/auth/tokens/current", auth=True)
        return True

    # -- SPLs ----------------------------------------------------------

    def list_spls(self) -> list[dict]:
        """Every SPL published on the marketplace (public endpoint)."""
        payload = self._request("GET", "/api/v1/spls")
        items = None
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            # Key lookup, not _pick: an explicitly empty list means "no SPLs
            # published", while a body with none of these keys is a contract
            # mismatch and must be reported, not read as an empty catalog.
            for key in ("spls", "items", "results", "data"):
                if key in payload:
                    items = payload[key]
                    break
        if not isinstance(items, list):
            raise MarketplaceError(
                "The marketplace returned an unexpected SPL list.",
                status=None,
                code=CODE_BAD_RESPONSE,
            )
        return [item for item in items if isinstance(item, dict)]

    def get_spl(self, name: str) -> dict:
        """One SPL by name (public endpoint), including its ``uvl`` block."""
        payload = self._request("GET", f"/api/v1/spls/{name}")
        body = self._expect_dict(payload, "SPL response")
        if isinstance(body.get("spl"), dict):
            body = body["spl"]
        if not body.get("name"):
            raise MarketplaceError(
                f"The marketplace response for SPL '{name}' did not include "
                "a name field.",
                status=None,
                code=CODE_BAD_RESPONSE,
            )
        return body

    def publish_spl(
        self,
        name: str,
        uvl_bytes: bytes,
        filename: str,
        description: str = "",
    ) -> dict:
        """Upload a new release of *name* with the given UVL model.

        Authenticated multipart upload, and the one call in this client that
        spends anything: on the far side the marketplace relays it to UVLHub
        with its own key and a DOI may be minted. ``splent spl:publish`` is its
        only caller and there is meant to be exactly one.
        """
        files = {
            UVL_FIELD_NAME: (filename, uvl_bytes, "text/plain"),
        }
        payload = self._request(
            "POST",
            f"/api/v1/spls/{name}/releases",
            auth=True,
            files=files,
            data={"description": description} if description else None,
            timeout=UPLOAD_TIMEOUT,
        )
        if payload is None:
            return {}
        return self._expect_dict(payload, "release response")
