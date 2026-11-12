"""MediaMart's online prices, from mediamart.vn's category listings.

MediaMart sells online at one price, so its products are listed at one
virtual store, ``ONLINE_STORE`` (as CellphoneS's and Hoàng Hà's).

What we read, and only that (robots.txt disallows only accounts and admin):

  * the product sitemaps (``sitemap_product_ph.xml``, ``_pm``, ``_pl``, ~30k
    pages): the first path segment of every product URL is a category
    ("smartphones", "tu-lanh", "laptop"…), ~280 of them.
  * each category page, once: its id, its name (the heading) and its brand
    filter (brand id and name).
  * the category's listing, 38 products a request, from ``POST
    /get-products`` (what its "Xem thêm" button loads), page after page
    until a page brings no new product; then the same listing filtered by
    each brand, whose products get that brand. A card gives the product's
    id, link, name, selling price, struck-through list price and image; it has
    no brand of its own, so a product under no brand filter keeps none.

Product pages are not read: the listing carries every price we keep.

MediaMart sells no used goods we could see ("-cu" in its slugs is a model
code: "cs-…-cu" is an outdoor unit, "bo-cu-sac" a charger); a name marked
"cũ" is left out all the same, as for Hoàng Hà. We keep facts and the URL of
the product's image on MediaMart's CDN, never the image (the operator's
decision for CellphoneS, 2026-11-09). The SKU is the product page's slug
(hashed past ``SKU_MAX``, as CellphoneS's).

Be gentle: one request at a time, ``min_interval_s`` apart.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
import time
from dataclasses import dataclass, replace

import httpx

from poi_lake.catalog.base import CatalogError, CatalogTransientError
from poi_lake.catalog.cellphones import sku_of
from poi_lake.catalog.winmart import Item

logger = logging.getLogger(__name__)

CHAIN = "mediamart"
BASE_URL = "https://mediamart.vn"
ONLINE_STORE = "online"
USER_AGENT = "poi-lake/0.1 (bizmap.live; price reference)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xml"}
SITEMAPS = ("/sitemap_product_ph.xml", "/sitemap_product_pm.xml", "/sitemap_product_pl.xml")
LISTING = "/get-products"

# The listing pages by the site's own size: asked for more, it still starts
# page n at (n - 1) * 38, so bigger pages only overlap.
PAGE_SIZE = 38
MAX_PAGES = 120
_USED_NAME = re.compile(r"(?:^|[\s(\[-])cũ(?:$|[\s)\]-])", re.I)
_LOC = re.compile(r"<loc>\s*https://mediamart\.vn/([^<\s]*)\s*</loc>")
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_OPTIONS = re.compile(r"currentSearchOption\s*=\s*(\{.*?\})\s*[;<\n]", re.S)
_H1 = re.compile(r"<h1[^>]*>\s*(.*?)\s*</h1>", re.S)
_BRAND = re.compile(r"<a\b[^>]*\bpa-filter-brand-(\d+)\b[^>]*>(.*?)</a>", re.S)
_ALT = re.compile(r"""\balt=(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""")
_CARD = re.compile(
    r"""<a\b[^>]*\bhref=["']?(/[^"'\s>]+)["']?[^>]*\bdata-id=["']?(\d+)["']?[^>]*>(.*?)</a>""", re.S
)
_NAME = re.compile(r"product-name[\"']?>\s*(.*?)\s*</p>", re.S)
_PRICE = re.compile(r"product-price[\"']?>\s*([^<]*?)\s*</p>")
_REGULAR = re.compile(r"product-price-regular[\"']?>\s*<span>\s*([^<]*?)\s*</span>")
# A card's ``src`` is a WebP rendition the CDN lacks for about one product in
# four; the page falls back to ``data-src``, the uploaded picture, on error.
# We keep the fallback, which is always there, else the rendition.
_IMG = re.compile(r"""<img\b[^>]*?\bsrc=["']?([^"'\s>]+)""")
_IMG_ORIGINAL = re.compile(r"""<img\b[^>]*?\bdata-src=["']?([^"'\s>]+)""")
_IMAGE = re.compile(r"^https://cdn\d*\.mediamart\.vn/\S+$")


def _text(s: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _money(s: str | None) -> float | None:
    digits = re.sub(r"\D", "", html_lib.unescape(s or ""))
    return float(digits) if digits and int(digits) > 0 else None


def candidates(sitemaps: list[str]) -> list[str]:
    """First path segments of the product sitemaps' two-segment URLs, once
    each, in order: the categories."""
    out: dict[str, None] = {}
    for xml in sitemaps:
        for path in _LOC.findall(xml):
            parts = path.strip("/").split("/")
            if len(parts) == 2 and _SEGMENT.match(parts[0]):
                out.setdefault(parts[0], None)
    return list(out)


@dataclass(frozen=True, slots=True)
class CategoryPage:
    id: str
    name: str
    brands: list[tuple[str, str]]  # (brand id, brand name)


def category_page(html: str, segment: str) -> CategoryPage | None:
    """A category page's id, name and brand filter; None when the page is no
    category page (a product page, a promotion)."""
    m = _OPTIONS.search(html)
    if not m:
        return None
    try:
        opts = json.loads(m.group(1))
    except ValueError:
        return None
    cid = opts.get("id") if isinstance(opts, dict) else None
    if not isinstance(cid, int) or opts.get("slugId") not in (segment, None):
        return None
    h1 = _H1.search(html)
    name = _text(h1.group(1)) if h1 else ""
    brands: dict[str, str] = {}
    for bid, inner in _BRAND.findall(html):
        alt = _ALT.search(inner)
        label = _text(next(g for g in alt.groups() if g is not None)) if alt else _text(inner)
        if label:
            brands.setdefault(bid, label)
    return CategoryPage(str(cid), name or segment, list(brands.items()))


@dataclass(frozen=True, slots=True)
class Card:
    product_id: str
    item: Item | None  # None: not on sale now ("Liên hệ"), or used goods


def parse_cards(html: str, menu: str, brand: str | None = None) -> list[Card]:
    """The product cards of a listing, each once; ``item`` only for new goods
    with a price."""
    out: dict[str, Card] = {}
    for href, pid, inner in _CARD.findall(html):
        if pid in out or "product-name" not in inner:
            continue
        path = html_lib.unescape(href).split("?", 1)[0].strip("/")
        slug = path.rsplit("/", 1)[-1]
        nm = _NAME.search(inner)
        name = _text(nm.group(1)) if nm else ""
        price_m = _PRICE.search(inner)
        selling = _money(price_m.group(1)) if price_m else None
        if not slug or not name or selling is None or _USED_NAME.search(name):
            out[pid] = Card(pid, None)
            continue
        reg = _REGULAR.search(inner)
        listed = _money(reg.group(1)) if reg else None
        price, sale = (listed, selling) if listed is not None and listed > selling else (selling, None)
        img = _IMG_ORIGINAL.search(inner) or _IMG.search(inner)
        image = html_lib.unescape(img.group(1) if img else "").strip()
        out[pid] = Card(pid, Item(
            sku=sku_of(slug),
            item_no=pid,
            name=name,
            brand=brand,
            barcode=None,
            uom=None,
            price=price,
            sale_price=sale,
            category_path=(menu,),
            menu=menu,
            seo_name=path,
            image_url=image if _IMAGE.match(image) else None,
        ))
    return list(out.values())


@dataclass(slots=True)
class Category:
    segment: str
    name: str
    items: list[Item]
    whole: bool  # every listing read answered: may retire what it no longer lists


class MediaMartClient:
    def __init__(
        self, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 1.0
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL, timeout=httpx.Timeout(60, connect=20), follow_redirects=True,
        )
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> MediaMartClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _send(self, method: str, path: str, body: dict[str, str] | None = None) -> str | None:
        """The answer's text; None when there is no such page."""
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.requests += 1
        try:
            if body is None:
                r = await self._client.get(path, headers=HEADERS)
            else:
                r = await self._client.post(path, headers=HEADERS, json=body)
        except httpx.TransportError as exc:
            raise CatalogTransientError(f"{path}: {exc}") from exc
        finally:
            self._next_at = time.monotonic() + self._min_interval_s
        if r.status_code in (429, 500, 502, 503, 504):
            raise CatalogTransientError(f"{path}: HTTP {r.status_code}")
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise CatalogError(f"{path}: HTTP {r.status_code}")
        return r.text

    async def candidates(self) -> list[str]:
        files = [await self._send("GET", p) or "" for p in SITEMAPS]
        found = candidates(files)
        if not found:
            raise CatalogError("no product pages in the sitemaps")
        return found

    async def _listing(
        self, cid: str, menu: str, brand: tuple[str, str] | None
    ) -> tuple[list[Item], bool]:
        """A category's listing (filtered by one brand) over all its pages,
        and whether every page answered."""
        seen: set[str] = set()
        out: list[Item] = []
        for page in range(1, MAX_PAGES + 1):
            body = {"id": cid, "page": str(page), "pageSize": str(PAGE_SIZE)}
            if brand:
                body["b"] = brand[0]
            html = await self._send("POST", LISTING, body)
            if html is None:
                return out, False
            cards = [
                c for c in parse_cards(html, menu, brand[1] if brand else None) if c.product_id not in seen
            ]
            if not cards:
                return out, True
            seen.update(c.product_id for c in cards)
            out.extend(c.item for c in cards if c.item is not None)
        return out, False  # more pages than MAX_PAGES: not read whole

    async def category(self, segment: str) -> Category | None:
        """A category's products, branded from its brand filter; None when the
        candidate is no category page."""
        first = await self._send("GET", f"/{segment}")
        page = category_page(first or "", segment)
        if page is None:
            return None
        out: dict[str, Item] = {}
        whole = True
        for brand in page.brands:
            items, ok = await self._listing(page.id, page.name, brand)
            whole = whole and ok
            for it in items:
                out.setdefault(it.sku, it)
        items, ok = await self._listing(page.id, page.name, None)
        whole = whole and ok
        for it in items:
            if it.sku in out:
                out[it.sku] = replace(out[it.sku], price=it.price, sale_price=it.sale_price)
            else:
                out[it.sku] = it
        return Category(segment, page.name, list(out.values()), whole)
