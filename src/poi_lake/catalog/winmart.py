"""WinMart / WinMart+ stores and per-store products, from winmart.vn's web API.

The site's own front end reads ``api-crownx.winmart.vn`` without a login:

  * ``/mt/api/web/v1/store-by-lat-long`` — stores within ~7 km of a point,
    200 per page (``pageNumber``). It answers 500 for some points; the
    neighbouring points cover those stores.
  * ``/mt/api/web/v1/store-by-chain`` — only the total store count (the list
    itself comes back empty). Used to check a sweep found everything.
  * ``/mt/api/web/v1/category`` — the menu tree.
  * ``/it/api/web/v3/item/category`` — one menu's items at one store, with
    price, sale price and barcode. Only items in stock are listed.

The store list holds only stores that take online orders (1,889 in 2026-10),
not every WinMart / WinMart+ — never treat a master missing from it as closed.

Be gentle: one request at a time, ``min_interval_s`` apart. We keep names,
barcodes, prices and categories, never images or descriptions.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

CHAIN = "winmart"
BASE_URL = "https://api-crownx.winmart.vn"
WEB_URL = "https://winmart.vn"
# Promotion menus repeat items from the real ones.
SKIP_MENUS = frozenset({"gia-sieu-re--c114", "uu-dai-hoi-vien--c51"})
STORE_PAGE = 200  # the store endpoint's fixed page size
ITEM_PAGE = 500
# store-by-lat-long answers within this radius; a point closer than
# REQUERY_KM to an earlier query adds little.
ANSWER_KM = 7.0
REQUERY_KM = 3.0


class CatalogError(Exception):
    """The chain's API answered something we cannot use."""


class CatalogTransientError(CatalogError):
    """Rate limited or temporarily down — retry later."""


@dataclass(frozen=True, slots=True)
class Store:
    code: str
    name: str
    store_type: str  # VMT = WinMart supermarket, VMP = WinMart+
    group_code: str
    address: str
    lat: float
    lng: float
    region_code: str  # the chain's province code: HNI, HCM, DNG…
    raw: dict[str, Any] = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class Item:
    sku: str
    item_no: str
    name: str
    brand: str | None
    barcode: str | None
    uom: str | None
    price: float | None
    sale_price: float | None
    category_path: tuple[str, ...]
    menu: str | None
    seo_name: str | None


def product_url(seo_name: str, store_code: str, group_code: str) -> str:
    return f"{WEB_URL}/products/{seo_name}?storeGroupCode={group_code}&storeCode={store_code}"


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular distance — plenty for a few km."""
    lat = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat)
    dy = math.radians(b[0] - a[0])
    return 6371.0 * math.hypot(dx, dy)


def parse_store(d: dict[str, Any]) -> Store | None:
    try:
        lat, lng = float(d["latitude"]), float(d["longitude"])
        code = str(d["storeCode"]).strip()
    except (KeyError, TypeError, ValueError):
        return None
    if not code or not (5 < lat < 25 and 100 < lng < 112):  # outside Vietnam = bad data
        return None
    return Store(
        code=code,
        name=" ".join(str(d.get("storeName") or "").split()),
        store_type=str(d.get("chainId") or ""),
        group_code=str(d.get("storeGroupCode") or ""),
        address=" ".join(str(d.get("officeAddress") or "").split()),
        lat=lat,
        lng=lng,
        region_code=str(d.get("provinceCode") or ""),
        raw=d,
    )


def _price(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_item(d: dict[str, Any], menu: str | None) -> Item | None:
    sku = str(d.get("sku") or "").strip()
    name = " ".join(str(d.get("name") or "").split())
    if not sku or not name:
        return None
    path = tuple(
        n for n in (d.get(f"mch{i}Name") for i in range(1, 6)) if isinstance(n, str) and n.strip()
    )
    return Item(
        sku=sku,
        item_no=str(d.get("itemNo") or ""),
        name=name,
        brand=(str(d["brandName"]).strip() or None) if d.get("brandName") else None,
        barcode=(str(d["barcode"]).strip() or None) if d.get("barcode") else None,
        uom=d.get("uomName") or None,
        price=_price(d.get("price")),
        sale_price=_price(d.get("salePrice")),
        category_path=path,
        menu=menu,
        seo_name=d.get("seoName") or None,
    )


class WinMartClient:
    def __init__(
        self, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 1.0
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30,
            headers={"User-Agent": "poi-lake/0.1 (bizmap.live)", "Accept": "application/json"},
        )
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> WinMartClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """The JSON body, or None when the API answers 500 for this query."""
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._next_at = time.monotonic() + self._min_interval_s
        self.requests += 1
        try:
            r = await self._client.get(path, params=params)
        except httpx.TransportError as exc:
            raise CatalogTransientError(f"{path}: {exc}") from exc
        if r.status_code in (429, 502, 503, 504):
            raise CatalogTransientError(f"{path}: HTTP {r.status_code}")
        if r.status_code == 500:
            return None
        if r.status_code != 200:
            raise CatalogError(f"{path}: HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as exc:
            raise CatalogError(f"{path}: not JSON") from exc

    # ---------------------------------------------------------------- stores

    async def store_total(self) -> int | None:
        body = await self._get("/mt/api/web/v1/store-by-chain", {"PageNumber": 1})
        try:
            return int(body["paging"]["totalCount"]) if body else None
        except (KeyError, TypeError, ValueError):
            return None

    async def stores_near(self, lat: float, lng: float) -> list[Store] | None:
        """Stores within ~7 km, or None when the API fails for this point."""
        out: list[Store] = []
        for page in range(1, 50):
            body = await self._get(
                "/mt/api/web/v1/store-by-lat-long",
                {"latitude": f"{lat:.5f}", "longitude": f"{lng:.5f}", "pageNumber": page},
            )
            if body is None:
                return out or None
            rows = (body.get("data") or {}).get("winMartStores") or []
            out.extend(s for s in map(parse_store, rows) if s is not None)
            if len(rows) < STORE_PAGE:
                break
        return out

    async def sweep_stores(
        self, seeds: Iterable[tuple[float, float]], *, max_queries: int = 5000
    ) -> tuple[dict[str, Store], dict[str, int]]:
        """Every store reachable from ``seeds``: query a point, then query
        around each newly found store that no earlier query point covers."""
        found: dict[str, Store] = {}
        queried: list[tuple[float, float]] = []
        failed = 0
        todo = list(seeds)

        def covered(p: tuple[float, float]) -> bool:
            return any(km_between(p, q) < REQUERY_KM for q in queried)

        while todo and len(queried) < max_queries:
            p = todo.pop()
            if covered(p):
                continue
            queried.append(p)
            stores = await self.stores_near(*p)
            if stores is None:
                failed += 1
                continue
            for s in stores:
                if s.code not in found:
                    found[s.code] = s
                    todo.append((s.lat, s.lng))
        stats = {"queries": len(queried), "failed_points": failed, "stores": len(found)}
        return found, stats

    # -------------------------------------------------------------- products

    async def menus(self) -> list[tuple[str, str]]:
        """Top-level (slug, name) menus, promotion menus left out."""
        body = await self._get("/mt/api/web/v1/category", {})
        if not body:
            raise CatalogError("menu tree unavailable")
        out = []
        for node in body.get("data") or []:
            parent = node.get("parent") or {}
            slug, name = parent.get("seoName"), parent.get("name")
            if slug and slug not in SKIP_MENUS:
                out.append((slug, name or slug))
        return out

    async def store_items(
        self, store_code: str, group_code: str, menus: list[tuple[str, str]]
    ) -> tuple[list[Item], list[str]]:
        """The store's items, and the menus the API failed on (500).

        A failed menu is skipped: its items keep their previous rows, since
        the caller only drops rows of menus that answered.
        """
        items: dict[str, Item] = {}
        failed: list[str] = []
        for slug, menu in menus:
            for page in range(1, 40):
                body = await self._get(
                    "/it/api/web/v3/item/category",
                    {"slug": slug, "storeCode": store_code, "storeGroupCode": group_code,
                     "pageNumber": page, "pageSize": ITEM_PAGE},
                )
                if body is None:
                    failed.append(menu)
                    break
                rows = (body.get("data") or {}).get("items") or []
                for row in rows:
                    item = parse_item(row, menu)
                    if item is not None:
                        items.setdefault(item.sku, item)
                total = (body.get("paging") or {}).get("totalCount") or 0
                if len(rows) < ITEM_PAGE or page * ITEM_PAGE >= total:
                    break
        if menus and len(failed) == len(menus):
            raise CatalogTransientError(f"items {store_code}: every menu failed")
        return list(items.values()), failed
