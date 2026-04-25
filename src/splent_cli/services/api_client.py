import os
from urllib.parse import quote

import requests


class SplentAPIError(RuntimeError):
    pass


def _base_url() -> str:
    return os.getenv("SPLENT_API_URL", "http://127.0.0.1:5000").rstrip("/")


def get(path: str):
    if not path.startswith("/"):
        raise ValueError("API path must start with '/'")

    try:
        response = requests.get(f"{_base_url()}{path}", timeout=10)
        response.raise_for_status()
        return response.json()

    except requests.exceptions.JSONDecodeError as exc:
        raise SplentAPIError("The SPLENT API returned an invalid JSON response.") from exc
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise SplentAPIError(f"SPLENT API returned HTTP {status}.") from exc
    except requests.exceptions.RequestException as exc:
        raise SplentAPIError(f"Could not connect to the SPLENT API: {exc}") from exc


def get_packages():
    return get("/api/packages")


def get_package_by_name(name: str):
    return get(f"/api/packages/{quote(name, safe='')}")
