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


def post_json(path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = _request("POST", path, json=body or {})
    r.raise_for_status()
    return r.json() if r.content else {}


def get_json(path: str, params: dict[str, Any] | None = None) -> Any:
    r = _request("GET", path, params=params)
    r.raise_for_status()
    return r.json()
