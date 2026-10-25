"""Bách Hoá Xanh stores, from bachhoaxanh.com's public store locator API.

``api.bachhoaxanh.com/gw`` answers without a login:

  * ``/Location/V2/GetFull`` — provinces, their districts and wards.
  * ``/Location/V2/GetStoresByLocation?provinceId&pageIndex&pageSize`` — a
    province's stores, 50 a page (``pageIndex`` from 0), with coordinates,
    address and opening hours, and the province's ``total``.

Only the store list: products need a login to pick a store, robots.txt
disallows store-specific pages, and the terms forbid copying the site's
content, so we read no product data from this chain.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from poi_lake.catalog.base import CatalogError, CatalogTransientError, Store

logger = logging.getLogger(__name__)

CHAIN = "bachhoaxanh"
BASE_URL = "https://api.bachhoaxanh.com/gw"
STORE_PAGE = 50  # the API's largest page


def parse_store(d: dict[str, Any]) -> Store | None:
    """A physical store, or None for a virtual one or unusable data."""
    if d.get("isStoreVirtual"):
        return None
    try:
        lat, lng = float(d["lat"]), float(d["lng"])
        code = str(d["storeId"]).strip()
    except (KeyError, TypeError, ValueError):
        return None
    if not code or not (5 < lat < 25 and 100 < lng < 112):
        return None
    address = " ".join(str(d.get("storeAddress") or "").split())
    # "BHX 193-195 Dương Văn Dương, Phường Phú Thọ Hòa, …" — the part before
    # the first comma is how the chain names the store.
    name = address.split(",", 1)[0].strip() or f"BHX {code}"
    return Store(
        code=code,
        name=name,
        store_type="BHX",
        group_code="",
        address=address,
        lat=lat,
        lng=lng,
        region_code=str(d.get("provinceId") or ""),
        raw=d,
        open_hours=" ".join(str(d.get("openHour") or "").split()) or None,
    )


class BHXClient:
    def __init__(
        self, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 1.0
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=60,
            headers={"User-Agent": "poi-lake/0.1 (bizmap.live)", "Accept": "application/json"},
        )
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> BHXClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._next_at = time.monotonic() + self._min_interval_s
        self.requests += 1
        try:
            r = await self._client.get(path, params=params)
        except httpx.TransportError as exc:
            raise CatalogTransientError(f"{path}: {exc}") from exc
        if r.status_code in (429, 500, 502, 503, 504):
            raise CatalogTransientError(f"{path}: HTTP {r.status_code}")
        if r.status_code != 200:
            raise CatalogError(f"{path}: HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as exc:
            raise CatalogError(f"{path}: not JSON") from exc
        if body.get("code") != 0 or not isinstance(body.get("data"), dict):
            raise CatalogError(f"{path}: code {body.get('code')!r}")
        return body["data"]

    async def provinces(self) -> list[tuple[int, str]]:
        data = await self._get("/Location/V2/GetFull", {})
        out = []
        for p in data.get("provinces") or []:
            try:
                out.append((int(p["id"]), str(p.get("nameNew") or p.get("name") or p["id"])))
            except (KeyError, TypeError, ValueError):
                continue
        if not out:
            raise CatalogError("province list empty")
        return out

    async def province_stores(self, province_id: int) -> tuple[list[Store], int]:
        """The province's stores and its published total (virtual ones included)."""
        out: list[Store] = []
        total = 0
        seen = 0
        for page in range(0, 200):
            data = await self._get(
                "/Location/V2/GetStoresByLocation",
                {"provinceId": province_id, "wardId": 0, "pageSize": STORE_PAGE, "pageIndex": page},
            )
            rows = data.get("stores") or []
            total = int(data.get("total") or 0)
            seen += len(rows)
            out.extend(s for s in map(parse_store, rows) if s is not None)
            if len(rows) < STORE_PAGE or seen >= total:
                break
        return out, total

    async def all_stores(self) -> tuple[dict[str, Store], dict[str, int]]:
        """Every province's stores. A province that fails is skipped and
        counted, so the caller can tell a partial list from a full one."""
        found: dict[str, Store] = {}
        published = 0
        failed = 0
        provinces = await self.provinces()
        for province_id, name in provinces:
            try:
                stores, total = await self.province_stores(province_id)
            except CatalogError as exc:
                logger.warning("bhx province %s (%s) failed: %s", province_id, name, exc)
                failed += 1
                continue
            published += total
            for s in stores:
                found[s.code] = s
        stats = {"provinces": len(provinces), "failed_provinces": failed,
                 "published_total": published, "stores": len(found)}
        return found, stats
