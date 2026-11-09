"""CellphoneS's online prices, from cellphones.com.vn's public product pages.

CellphoneS sells online at one price across the chain, so its products are
listed at one virtual store, ``ONLINE_STORE`` (as Con Cưng's).

What we read, and only that:

  * ``/sitemap/sitemap_index.xml`` and the ``product-sitemap*.xml`` files it
    lists (about 65, a thousand pages each): the product pages. Used goods
    ("…-cu-tray-xuoc.html", "…-cu-xuoc-can.html") are left out, as the
    operator chose (2026-11-09). Every page's ``lastmod`` is the day the file
    was made, so it says nothing about which pages changed.
  * each product page: its JSON-LD (name, sku, brand, selling price, stock,
    image), its breadcrumb (the categories) and the struck-through list price
    shown beside the selling price.

Category pages show a few dozen products and load the rest from ``/lapi/``,
which robots.txt disallows, as it does every URL with a ``?``: a product page
is the only allowed place for a price. A page is 120 KB on the wire, so a run
reads at most ``max_pages`` of them and a product is re-read every few days
(the worker picks the pages read longest ago first).

We keep facts: names, brands, categories, prices, and the URL of the
product's image on CellphoneS's CDN, which bizmap links to (the operator's
decision, 2026-11-09); the image itself is never copied. The JSON-LD "sku" is
the page's slug, not a barcode: ``barcode`` stays empty. The slug (hashed when
longer than ``SKU_MAX``) is our SKU; the whole slug is ``seo_name``.

Be gentle: one request at a time, ``min_interval_s`` apart.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import logging
import re
import time
from typing import Any

import httpx

from poi_lake.catalog.base import CatalogError, CatalogTransientError
from poi_lake.catalog.winmart import Item

logger = logging.getLogger(__name__)

CHAIN = "cellphones"
BASE_URL = "https://cellphones.com.vn"
ONLINE_STORE = "online"
USER_AGENT = "poi-lake/0.1 (bizmap.live; price reference)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xml"}

# A product page: one path segment. Category pages have two or more, or are
# not in the product sitemaps.
PRODUCT_URL = re.compile(r"^https://cellphones\.com\.vn/([a-z0-9][a-z0-9-]*)\.html$")
PRODUCT_SITEMAP = re.compile(r"^https://cellphones\.com\.vn/sitemap/product-sitemap\d*\.xml$")
# "cũ" (used) as a word of the slug: -cu-tray-xuoc, -cu-xuoc-can, -cu-dep, -cu.
USED_SLUG = re.compile(r"(?:^|-)cu(?:-|$)")
USED_CATEGORY = "hàng cũ"
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LD_JSON = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)
_BASE_PRICE = re.compile(r'class="[^"]*\bbase-price\b[^"]*"[^>]*>\s*([\d.]+)\s*(?:đ|&#273;|₫)')
_IMAGE = re.compile(r"^https://cdn\d*\.cellphones\.com\.vn/\S+$")


def sitemap_files(index: str) -> list[str]:
    """The product sitemaps a sitemap index lists, in its order."""
    return list(dict.fromkeys(u for u in _LOC.findall(index) if PRODUCT_SITEMAP.match(u)))


def product_slugs(sitemap: str) -> list[str]:
    """The new-goods product pages a product sitemap lists, as slugs."""
    out: dict[str, None] = {}
    for loc in _LOC.findall(sitemap):
        m = PRODUCT_URL.match(loc)
        if m and not USED_SLUG.search(m.group(1)):
            out.setdefault(m.group(1), None)
    return list(out)


def _money(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


SKU_MAX = 60  # chain_products.sku


def sku_of(slug: str) -> str:
    """The product's key: its slug, or, past ``SKU_MAX``, the slug's start and
    a hash of the whole (a tenth of the slugs are longer). The full slug is
    kept as ``seo_name``."""
    if len(slug) <= SKU_MAX:
        return slug
    digest = hashlib.sha1(slug.encode()).hexdigest()[:10]
    return f"{slug[: SKU_MAX - 11]}-{digest}"


def _ld_objects(html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in _LD_JSON.findall(html):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        for x in d if isinstance(d, list) else [d]:
            if isinstance(x, dict):
                out.append(x)
    return out


def parse_product(html: str, slug: str) -> Item | None:
    """A product page as an item, ``price`` None when it is not on sale now
    (out of stock, "Liên hệ để báo giá"). None when the page is no product
    page or sells used goods.

    ``price`` is the list price (the struck-through one) and ``sale_price``
    what it sells at, when lower, as for Con Cưng."""
    ld = _ld_objects(html)
    product = next((x for x in ld if x.get("@type") == "Product" and x.get("offers")), None)
    if product is None:
        return None
    offers = product["offers"]
    if not isinstance(offers, dict) or offers.get("@type") != "Offer":
        return None  # a category page's AggregateOffer
    name = " ".join(html_lib.unescape(str(product.get("name") or "")).split())
    if not name:
        return None
    crumbs = next((x for x in ld if x.get("@type") == "BreadcrumbList"), None)
    names = [
        " ".join(html_lib.unescape(str((e.get("item") or {}).get("name") or e.get("name") or "")).split())
        for e in (crumbs or {}).get("itemListElement") or []
        if isinstance(e, dict)
    ]
    # Home first, the product last: what is between are its categories.
    path = tuple(n for n in names[1:-1] if n)
    if any(n.casefold() == USED_CATEGORY for n in path):
        return None
    condition = str(offers.get("itemCondition") or "")
    if condition and not condition.endswith("NewCondition"):
        return None
    selling = _money(offers.get("price"))
    if "InStock" not in str(offers.get("availability") or "InStock"):
        selling = None
    listed = None
    if selling is not None:
        m = _BASE_PRICE.search(html)
        listed = _money(m.group(1).replace(".", "")) if m else None
    if listed is not None and selling is not None and listed > selling:
        price, sale = listed, selling
    else:
        price, sale = selling, None
    brand = product.get("brand")
    brand_name = brand.get("name") if isinstance(brand, dict) else brand
    image = product.get("image")
    image = image[0] if isinstance(image, list) and image else image
    image = str(image or "").strip()
    return Item(
        sku=sku_of(slug),
        item_no="",
        name=name,
        brand=" ".join(str(brand_name or "").split()) or None,
        barcode=None,  # the JSON-LD sku is the page's slug, not an EAN
        uom=None,
        price=price,
        sale_price=sale,
        category_path=path,
        menu=path[0] if path else "Khác",  # never empty: save_items retires empty menus
        seo_name=slug,
        image_url=image if _IMAGE.match(image) else None,
    )


class GoneError(CatalogError):
    """The product page is no more (404, 410, or moved elsewhere)."""


class CellphoneSClient:
    def __init__(
        self, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 1.0
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(60, connect=20),
            follow_redirects=False,  # a removed product redirects elsewhere
        )
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> CellphoneSClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> str:
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.requests += 1
        try:
            r = await self._client.get(path, headers=HEADERS)
        except httpx.TransportError as exc:
            raise CatalogTransientError(f"{path}: {exc}") from exc
        finally:
            self._next_at = time.monotonic() + self._min_interval_s
        if r.status_code in (429, 500, 502, 503, 504):
            raise CatalogTransientError(f"{path}: HTTP {r.status_code}")
        if r.status_code in (301, 302, 307, 308, 404, 410):
            raise GoneError(f"{path}: HTTP {r.status_code}")
        if r.status_code != 200:
            raise CatalogError(f"{path}: HTTP {r.status_code}")
        return r.text

    async def products(self) -> tuple[list[str], bool]:
        """Every new-goods product slug the sitemaps list, and whether every
        sitemap file was read (only then may a product missing from them be
        retired)."""
        files = sitemap_files(await self._get("/sitemap/sitemap_index.xml"))
        if not files:
            raise CatalogError("no product sitemaps in the index")
        out: dict[str, None] = {}
        whole = True
        for url in files:
            try:
                xml = await self._get(url.removeprefix(BASE_URL))
            except CatalogError as exc:
                logger.info("cellphones %s: %s", url, exc)
                whole = False
                continue
            for slug in product_slugs(xml):
                out.setdefault(slug, None)
        return list(out), whole

    async def product(self, slug: str) -> Item | None:
        """The product page read as an item; raises ``GoneError`` when it is no more."""
        return parse_product(await self._get(f"/{slug}.html"), slug)
