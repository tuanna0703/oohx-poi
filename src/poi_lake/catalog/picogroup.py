"""Pico's and Nguyễn Kim's online prices, and Pico's stores.

pico.vn and nguyenkim.com are one shop on one platform: the same product
codes ("00003518"), the same catalogue API, told apart by the
``x-legal-entity-code`` header the sites send (``PICO``, ``NK``), which picks
each chain's assortment. Both sell online at one price, so their products are
listed at one virtual store, ``ONLINE_STORE`` (as CellphoneS's).

What we read, and only that (robots.txt allows the product pages; the API
hosts have no robots.txt):

  * ``catalog.pico.vn/user/api/products``, 100 products a request (what the
    sites' category pages load): code, name, category, barcode (GTIN), list
    price, selling price, stock, picture, page slug. Every product, daily.
  * the product page, once per product code, for its brand (the API carries
    no brand): the JSON-LD ``brand``. A code's brand serves both chains.
  * ``ecommerce.pico.vn/user/api/store`` (what pico.vn/store loads): the
    group's locations; Pico's own are those of legal entity ``PICO``.

Prices: ``price`` is the list price, ``salePrice`` what it sells at. The
API's ``finalPrice`` folds in hour-long flash sales and coupons and is the
same whatever the chain, while the two sites show different promotional
prices for one product: we keep the selling price, as both price it.

We keep facts and the URL of the product's picture on the group's CDN, never
the picture (the operator's decision, 2026-11-09). Used goods, warranty
packages and gifts are left out.
The SKU is the product code; the page's path is ``seo_name``.

Be gentle: one request at a time, ``min_interval_s`` apart.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from poi_lake.catalog.base import CatalogError, CatalogTransientError, Store
from poi_lake.catalog.winmart import Item

logger = logging.getLogger(__name__)

ONLINE_STORE = "online"
USER_AGENT = "poi-lake/0.1 (bizmap.live; price reference)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html"}
PRODUCTS_API = "https://catalog.pico.vn/user/api/products"
STORES_API = "https://ecommerce.pico.vn/user/api/store"
PAGE_SIZE = 100
MAX_PAGES = 200
SKU_MAX = 60  # chain_products.sku


@dataclass(frozen=True, slots=True)
class Site:
    chain: str
    base_url: str
    legal_entity: str


SITES: dict[str, Site] = {
    "pico": Site("pico", "https://pico.vn", "PICO"),
    "nguyenkim": Site("nguyenkim", "https://www.nguyenkim.com", "NK"),
}

# Not goods: extended-warranty packages ("BHMR", "Gói BH 2 năm …") and the
# gifts given with a purchase ("KM_Gia dụng").
_NOT_GOODS = re.compile(r"^(?:BHMR$|KM_)")
_USED_NAME = re.compile(r"(?:^|[\s(\[-])cũ(?:$|[\s)\]-])", re.I)
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_IMAGE = re.compile(r"^https://cdn\.(?:pico\.vn|nguyenkimmall\.com)/\S+$")
_LD_JSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)


def _vi(v: Any) -> str:
    """A localised field ({"vi": …, "en": …}) or a plain string, tidied."""
    if isinstance(v, dict):
        v = v.get("vi") or v.get("en")
    return " ".join(html_lib.unescape(str(v or "")).split())


def _money(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def page_path(d: dict[str, Any]) -> str | None:
    """The product page's path: its slug and code postfix
    ("may-say-…-td-72vwd-00003518")."""
    slugs = d.get("slugs") or {}
    path = _vi(slugs.get("value")) + str(slugs.get("postfix") or "")
    return path if _PATH.match(path) else None


def parse_product(d: dict[str, Any], brand: str | None = None) -> Item | None:
    """One API product as an item, ``price`` None when not sold now (out of
    stock, no price); None for used goods or a product with no code."""
    code = str(d.get("code") or "").strip()
    name = _vi(d.get("name"))
    if not _CODE.match(code) or len(code) > SKU_MAX or not name or _USED_NAME.search(name):
        return None
    menu = _vi((d.get("cat") or {}).get("name")) or "Khác"  # never empty: save_items retires empty menus
    if _NOT_GOODS.match(menu) or name.startswith("Gói BH"):
        return None
    selling = _money(d.get("salePrice"))
    if d.get("stockStatus") not in (None, "inStock"):
        selling = None
    listed = _money(d.get("price"))
    if selling is not None and listed is not None and listed > selling:
        price, sale = listed, selling
    else:
        price, sale = selling, None
    image = str(d.get("thumbnail") or "").strip()
    gtin = re.sub(r"\D", "", str(d.get("gtin") or ""))
    return Item(
        sku=code,
        item_no=code,
        name=name,
        brand=brand,
        barcode=gtin if 8 <= len(gtin) <= 14 else None,
        uom=None,
        price=price,
        sale_price=sale,
        category_path=(menu,),
        menu=menu,
        seo_name=page_path(d),
        image_url=image if _IMAGE.match(image) else None,
    )


def page_brand(html: str) -> str | None:
    """The brand a product page's JSON-LD names ("CASPER")."""
    for raw in _LD_JSON.findall(html):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict) and x.get("@type") == "Product":
                b = x.get("brand")
                name = _vi(b.get("name") if isinstance(b, dict) else b)
                return name or None
    return None


def parse_store(d: dict[str, Any]) -> Store | None:
    """One location of the group's store list, as a store; None without a place."""
    try:
        lat, lng = float(d["lat"]), float(d["lng"])
    except (KeyError, TypeError, ValueError):
        return None
    code = str(d.get("code") or "").strip()
    if not code or not (8 <= lat <= 24 and 102 <= lng <= 110):
        return None
    return Store(
        code=code,
        name=_vi(d.get("name")),
        store_type=str(d.get("type") or ""),
        group_code=str(d.get("legalEntityCode") or ""),
        address=_vi(d.get("fullAddress")) or _vi(d.get("street")),
        lat=lat,
        lng=lng,
        region_code=str(d.get("provinceCode") or d.get("regionCode") or ""),
        raw=d,
        phone=str(d.get("phoneNumber") or "") or None,
    )


class GoneError(CatalogError):
    """The product page is no more."""


class PicoGroupClient:
    def __init__(
        self, site: Site, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 1.0
    ) -> None:
        self.site = site
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=20), follow_redirects=True,
        )
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> PicoGroupClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(self, url: str, *, entity: bool = False) -> httpx.Response:
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.requests += 1
        headers = dict(HEADERS)
        if entity:
            headers["x-legal-entity-code"] = self.site.legal_entity
        try:
            r = await self._client.get(url, headers=headers)
        except httpx.TransportError as exc:
            raise CatalogTransientError(f"{url}: {exc}") from exc
        finally:
            self._next_at = time.monotonic() + self._min_interval_s
        if r.status_code in (429, 500, 502, 503, 504):
            raise CatalogTransientError(f"{url}: HTTP {r.status_code}")
        if r.status_code in (404, 410):
            raise GoneError(f"{url}: HTTP {r.status_code}")
        if r.status_code != 200:
            raise CatalogError(f"{url}: HTTP {r.status_code}")
        return r

    async def products(self) -> tuple[list[dict[str, Any]], bool]:
        """The chain's whole assortment as the API gives it, and whether it
        is whole (every page read, as many products as the API counts)."""
        out: dict[str, dict[str, Any]] = {}
        total = None
        for page in range(1, MAX_PAGES + 1):
            r = await self._get(f"{PRODUCTS_API}?page={page}&limit={PAGE_SIZE}", entity=True)
            try:
                data = r.json()["data"]
                rows, pages, total = data["data"], int(data["pages"]), int(data["total"])
            except (ValueError, KeyError, TypeError) as exc:
                raise CatalogError(f"products page {page}: {exc}") from exc
            for d in rows:
                if isinstance(d, dict) and d.get("code"):
                    out.setdefault(str(d["code"]), d)
            if page >= pages or not rows:
                break
        whole = total is not None and len(out) >= total
        return list(out.values()), whole

    async def brand(self, path: str) -> str | None:
        """A product page's brand; raises ``GoneError`` when the page is no more."""
        r = await self._get(f"{self.site.base_url}/{path}")
        return page_brand(r.text)

    async def stores(self) -> list[Store]:
        """The chain's own locations from the group's store list."""
        r = await self._get(f"{STORES_API}?")
        try:
            rows = r.json()["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise CatalogError(f"stores: {exc}") from exc
        out = []
        for d in rows if isinstance(rows, list) else []:
            if isinstance(d, dict) and d.get("legalEntityCode") == self.site.legal_entity and d.get("status") == 1:
                s = parse_store(d)
                if s:
                    out.append(s)
        return out
