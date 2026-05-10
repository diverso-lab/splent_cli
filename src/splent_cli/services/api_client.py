import os
from urllib.parse import quote

import requests


class SplentAPIError(RuntimeError):
    pass


class SplentAPIAuthError(SplentAPIError):
    pass


def _base_url() -> str:
    return os.getenv("SPLENT_API_URL", "http://127.0.0.1:5000").rstrip("/")


def _headers() -> dict[str, str]:
    if os.getenv("SPLENT_MARKETPLACE_AUTH") != "true":
        return {}

    token = (os.getenv("SPLENT_API_TOKEN") or "").strip().strip("\"'")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _request(method: str, path: str, json_body: dict | None = None):
    if not path.startswith("/"):
        raise ValueError("API path must start with '/'")

    try:
        response = requests.request(
            method,
            f"{_base_url()}{path}",
            timeout=10,
            headers=_headers(),
            json=json_body,
        )
        response.raise_for_status()

        if not response.content:
            return {}

        return response.json()

    except requests.exceptions.JSONDecodeError as exc:
        raise SplentAPIError("The SPLENT API returned an invalid JSON response.") from exc
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        if status in {401, 403}:
            raise SplentAPIAuthError(
                "Marketplace login required. Run: splent marketplace:login"
            ) from exc
        raise SplentAPIError(f"SPLENT API returned HTTP {status}.") from exc
    except requests.exceptions.RequestException as exc:
        raise SplentAPIError(f"Could not connect to the SPLENT API: {exc}") from exc


def get(path: str):
    return _request("GET", path)


def post(path: str, json: dict | None = None):
    return _request("POST", path, json_body=json)


def get_packages():
    return get("/api/packages")


def get_package_by_name(name: str):
    return get(f"/api/packages/{quote(name, safe='/')}")
