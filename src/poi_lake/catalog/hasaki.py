"""Hasaki's online prices, from hasaki.vn's public product pages.

Hasaki sells online at one price across the chain, so its products are
listed at one virtual store, ``ONLINE_STORE`` (as CellphoneS's).

What we read, and only that (robots.txt allows both; the operator chose to
read them, 2026-11-14, knowing the site's terms restrict commercial use of
its material: we keep facts, not its texts or pictures):

  * ``/sitemap.xml`` and the category sitemaps it lists
    (``/sitemap/<category>-c<id>.xml``) plus ``/sitemap/canonical.xml``: the
    product pages on sale on the web, about 7,500. Not
    ``product-off-web.xml`` (44,000 pages, every one we sampled out of
    stock), nor the spa and clinic services' sitemaps.
  * each product page: its JSON-LD (name, Hasaki's own sku, ``mpn``, which
    is the EAN barcode, brand, price, picture), its breadcrumb (the
    categories), and its ``product:availability`` meta tag. The JSON-LD says
    "InStock" whatever the stock; the meta tag tells.

One price: the page carries only what the product sells at; a struck-through
list price is drawn in the browser from ``/ajax/``, which robots.txt
disallows. ``sale_price`` stays empty.

The SKU is the product number that ends the page's path
(``…-85ml-113073.html`` → ``113073``), which stays when the slug's words
change; the path is ``seo_name`` and Hasaki's own sku ``item_no``. We keep
the URL of the product's picture on Hasaki's CDN, never the picture. Gifts
("[GIFT] …", brand "Quà Tặng") are left out.

Be gentle: one request at a time, ``min_interval_s`` apart.
"""

from __future__ import annotations

import asyncio
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

CHAIN = "hasaki"
BASE_URL = "https://hasaki.vn"
ONLINE_STORE = "online"
USER_AGENT = "poi-lake/0.1 (bizmap.live; price reference)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xml"}

PRODUCT_URL = re.compile(r"^https://hasaki\.vn/san-pham/([a-z0-9][a-z0-9-]*-(\d+))\.html$")
# The category sitemaps, and the canonical one; not product-off-web.xml.
PRODUCT_SITEMAP = re.compile(r"^https://hasaki\.vn/sitemap/(?:[a-z0-9-]+-c\d+|canonical)\.xml$")
# Services, not goods: clinic, laser hair removal, spa and relaxation packages.
SERVICE_SITEMAPS = frozenset({
    "dich-vu-phong-kham-c1839", "triet-long-diode-laser-c1835", "thu-gian-cham-soc-c1837",
    "goi-thu-gian-c2318",
})
_GIFT_NAME = re.compile(r"^\s*\[\s*(?:gift|quà\s*tặng|qua\s*tang)\s*\]", re.I)
GIFT_BRANDS = frozenset({"quà tặng"})
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LD_JSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.S)
_AVAILABILITY = re.compile(r'<meta\s+name="product:availability"\s+content="([^"]*)"')
_IMAGE = re.compile(r"^https://media\.hcdn\.vn/\S+\.(?:jpe?g|png|webp|gif)$", re.I)


def sitemap_files(index: str) -> list[str]:
    """The sitemaps of goods on sale the index lists, in its order."""
    out: dict[str, None] = {}
    for u in _LOC.findall(index):
        if PRODUCT_SITEMAP.match(u) and u.rsplit("/", 1)[-1].removesuffix(".xml") not in SERVICE_SITEMAPS:
            out.setdefault(u, None)
    return list(out)


def product_slugs(sitemap: str) -> list[str]:
    """The product pages a sitemap lists, as paths without ``.html``."""
    out: dict[str, None] = {}
    for loc in _LOC.findall(sitemap):
        m = PRODUCT_URL.match(loc)
        if m:
            out.setdefault(m.group(1), None)
    return list(out)


def sku_of(slug: str) -> str:
    """The product number that ends the page's path."""
    return slug.rsplit("-", 1)[-1]


def valid_gtin(code: str) -> bool:
    """An EAN-8, UPC-A, EAN-13 or GTIN-14 whose check digit adds up."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14) or not code.strip("0"):
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _money(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 1000 else None  # 0 and token prices (9 đ) are not prices


def _text(v: Any) -> str:
    return " ".join(html_lib.unescape(str(v or "")).split())


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
    (out of stock, no price). None when the page is no product page or a gift."""
    ld = _ld_objects(html)
    product = next((x for x in ld if x.get("@type") == "Product"), None)
    if product is None:
        return None
    offers = product.get("offers")
    if not isinstance(offers, dict):
        return None
    name = _text(product.get("name"))
    brand = product.get("brand")
    brand_name = _text(brand.get("name") if isinstance(brand, dict) else brand) or None
    if not name or _GIFT_NAME.match(name) or (brand_name or "").casefold() in GIFT_BRANDS:
        return None
    crumbs = next((x for x in ld if x.get("@type") == "BreadcrumbList"), None)
    names = [
        _text((e.get("item") or {}).get("name") if isinstance(e.get("item"), dict) else e.get("name"))
        for e in (crumbs or {}).get("itemListElement") or []
        if isinstance(e, dict)
    ]
    # The categories, then the product itself last.
    path = tuple(n for n in names[:-1] if n)
    price = _money(offers.get("price"))
    m = _AVAILABILITY.search(html)
    if m and m.group(1).strip().casefold() != "in stock":
        price = None
    gtin = re.sub(r"\D", "", str(product.get("mpn") or ""))
    image = product.get("image")
    image = image[0] if isinstance(image, list) and image else image
    image = str(image or "").strip()
    return Item(
        sku=sku_of(slug),
        item_no=_text(product.get("sku"))[:60],
        name=name,
        brand=brand_name,
        barcode=gtin if valid_gtin(gtin) else None,
        uom=None,
        price=price,
        sale_price=None,
        category_path=path,
        # The second level ("Chăm Sóc Cá Nhân"): the first, "Sức Khỏe - Làm
        # Đẹp", holds most of the shop. Never empty: save_items retires empty menus.
        menu=(path[1] if len(path) > 1 else path[0]) if path else "Khác",
        seo_name=slug,
        image_url=image if _IMAGE.match(image) else None,
    )


class GoneError(CatalogError):
    """The product page is no more (404, 410, or moved elsewhere)."""


class HasakiClient:
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

    async def __aenter__(self) -> HasakiClient:
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
        """Every product page of goods on sale the sitemaps list, and whether
        every sitemap file was read (only then may a product missing from
        them be retired)."""
        files = sitemap_files(await self._get("/sitemap.xml"))
        if not files:
            raise CatalogError("no product sitemaps in the index")
        out: dict[str, None] = {}
        whole = True
        for url in files:
            try:
                xml = await self._get(url.removeprefix(BASE_URL))
            except CatalogError as exc:
                logger.info("hasaki %s: %s", url, exc)
                whole = False
                continue
            for slug in product_slugs(xml):
                out.setdefault(slug, None)
        return list(out), whole

    async def product(self, slug: str) -> Item | None:
        """The product page read as an item; raises ``GoneError`` when it is no more."""
        return parse_product(await self._get(f"/san-pham/{slug}.html"), slug)
