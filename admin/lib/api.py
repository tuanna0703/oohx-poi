"""Admin HTTP client — calls the api service for actions that mutate state.

Reads (KPIs, tables, search) hit the DB directly via ``admin.lib.db``;
writes go through the admin API so business logic (validation, dramatiq
dispatch, etc.) stays in one place.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


def _api_base() -> str:
    return os.getenv("POI_LAKE_API_URL", "http://api:8000").rstrip("/")


def _admin_token() -> str:
    token = os.getenv("APP_SECRET_KEY")
    if not token:
        raise RuntimeError(
            "APP_SECRET_KEY is not set in the admin container; cannot authenticate"
        )
    return token


def _request(method: str, path: str, **kwargs) -> httpx.Response:
    url = f"{_api_base()}{path}"
    headers = kwargs.pop("headers", {}) | {"X-Admin-Token": _admin_token()}
    with httpx.Client(timeout=30) as c:
        return c.request(method, url, headers=headers, **kwargs)


class APIError(RuntimeError):
    """HTTP error that carries the API's ``detail`` field when present, so
    Streamlit can show *why* a request was rejected instead of a generic
    ``400 Bad Request``."""

    def __init__(self, status_code: int, detail: str, url: str) -> None:
        super().__init__(f"{status_code} — {detail}")
        self.status_code = status_code
        self.detail = detail
        self.url = url


def _raise_for_status(r: httpx.Response) -> None:
    if r.is_success:
        return
    detail: str
    try:
        body = r.json()
        detail = body.get("detail") if isinstance(body, dict) else str(body)
        if not isinstance(detail, str):
            detail = str(detail)
    except Exception:  # noqa: BLE001
        detail = (r.text or "").strip() or r.reason_phrase
    raise APIError(r.status_code, detail, str(r.request.url))


def post_json(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = _request("POST", path, json=body or {})
    _raise_for_status(r)
    return r.json() if r.content else {}


def get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    r = _request("GET", path, params=params)
    _raise_for_status(r)
    return r.json()
