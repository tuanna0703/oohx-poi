"""Hoàng Hà Mobile's online prices, from hoanghamobile.com's category pages.

Hoàng Hà sells online at one price, so its products are listed at one virtual
store, ``ONLINE_STORE`` (as Con Cưng's and CellphoneS's).

What we read, and only that:

  * ``/sitemap.xml``: one file of every page. Category pages are not marked
    as such, so the first path segment of every two-segment URL is a
    candidate ("dien-thoai-di-dong", "laptop", "tivi"…); a candidate whose page
    holds a product listing (``data-category-listing``) is a category.
  * each category page, 20 products a page, then ``?p=2``, ``?p=3`` … (what
    its "Xem thêm" button loads; robots.txt only disallows ``filters=``),
    until a page brings no new product. A card gives the product's id, link,
    name, selling price, struck-through list price and image.
  * the category's brand pages, linked from its brand bar
    (``/dien-thoai-di-dong/oppo``, labelled "OPPO"), read the same way: their
    products get that brand. A card has no brand of its own, so a product
    under no brand page keeps none.

Product pages are not read: their cards for related products carry the same
price markup, and a page lists one price per colour.

Used goods (the "kho-san-pham-cu" store, names marked "cũ") are left out, as
the operator chose for CellphoneS (2026-11-09). We keep facts and the URL of
the product's image on Hoàng Hà's CDN, never the image (same decision).
The SKU is the product page's slug (hashed past ``SKU_MAX``, as CellphoneS's).

Be gentle: one request at a time, ``min_interval_s`` apart.
"""

from __future__ import annotations

import asyncio
import html as html_lib
import logging
import re
import time
from dataclasses import dataclass, replace

import httpx

from poi_lake.catalog.base import CatalogError, CatalogTransientError
from poi_lake.catalog.cellphones import sku_of
from poi_lake.catalog.winmart import Item

logger = logging.getLogger(__name__)

CHAIN = "hoangha"
BASE_URL = "https://hoanghamobile.com"
ONLINE_STORE = "online"
USER_AGENT = "poi-lake/0.1 (bizmap.live; price reference)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xml"}

MAX_PAGES = 80
USED_CATEGORIES = frozenset({"kho-san-pham-cu"})
_USED_NAME = re.compile(r"(?:^|[\s(\[-])cũ(?:$|[\s)\]-])", re.I)
_LOC = re.compile(r"<loc>\s*https://hoanghamobile\.com/([^<\s]*)\s*</loc>")
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CARD = re.compile(r"<article\b[^>]*\bdata-category-product\b[^>]*>(.*?)</article>", re.S)
_CARD_ID = re.compile(r'data-product-id="(\d+)"')
_CARD_LINK = re.compile(r'<a\b[^>]*\bhref="(/[^"]+)"[^>]*\bdata-product-detail-link\b[^>]*>([^<]+)</a>')
_CARD_PRICE = re.compile(r"data-product-price>\s*([^<]*?)\s*</p>")
_CARD_LAST = re.compile(r"data-product-last-price>\s*([^<]*?)\s*</p>")
_CARD_IMAGE = re.compile(r'<img\b[^>]*\bdata-product-image\b[^>]*\bsrc="([^"]+)"|<img\b[^>]*\bsrc="([^"]+)"[^>]*\bdata-product-image\b')
_IMAGE = re.compile(r"^https://i\d*\.hoanghamobile\.vn/\S+$")
_H1 = re.compile(r"<h1[^>]*>\s*(.*?)\s*</h1>", re.S)
# The brand bar runs from its slider to the slider's nav buttons.
_BRAND_BAR = re.compile(r"data-category-brand-slider(.*?)swiper-button", re.S)
_CRUMB = re.compile(r'"@type"\s*:\s*"ListItem"\s*,\s*"position"\s*:\s*(\d+)\s*,\s*"name"\s*:\s*"([^"]+)"')
_BRAND_LINK = re.compile(r'<a\b[^>]*\bhref="/([a-z0-9-]+)/([a-z0-9-]+)"[^>]*>(.*?)</a>', re.S)
_ALT = re.compile(r'\b(?:alt|title|aria-label)="([^"]+)"')


def _text(s: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def _money(s: str | None) -> float | None:
    digits = re.sub(r"\D", "", html_lib.unescape(s or ""))
    return float(digits) if digits and int(digits) > 0 else None


def candidates(sitemap: str) -> list[str]:
    """First path segments of the sitemap's two-segment URLs, once each, in
    order: the category pages are among them."""
    out: dict[str, None] = {}
    for path in _LOC.findall(sitemap):
        parts = path.strip("/").split("/")
        if len(parts) == 2 and _SEGMENT.match(parts[0]) and parts[0] not in USED_CATEGORIES:
            out.setdefault(parts[0], None)
    return list(out)


def is_category(html: str) -> bool:
    return "data-category-listing" in html


def category_name(html: str) -> str | None:
    """The category's name as its breadcrumb ends ("Điện thoại"), else its
    heading ("Điện thoại chính hãng")."""
    crumbs = sorted((int(n), _text(name)) for n, name in _CRUMB.findall(html))
    if len(crumbs) >= 2 and crumbs[-1][1]:
        return crumbs[-1][1]
    m = _H1.search(html)
    name = _text(m.group(1)) if m else ""
    return name or None


def brand_pages(html: str, segment: str) -> list[tuple[str, str]]:
    """The category's brand bar: (path, brand) — ("dien-thoai-di-dong/oppo", "OPPO")."""
    bar = _BRAND_BAR.search(html)
    out: dict[str, str] = {}
    for top, sub, inner in _BRAND_LINK.findall(bar.group(1) if bar else ""):
        if top != segment:
            continue
        alt = _ALT.search(inner) or _ALT.search(html_lib.unescape(inner))
        label = _text(alt.group(1)) if alt else _text(inner)
        if label:
            out.setdefault(f"{top}/{sub}", label)
    return list(out.items())


@dataclass(frozen=True, slots=True)
class Card:
    product_id: str
    item: Item


def parse_cards(html: str, menu: str, brand: str | None = None) -> list[Card]:
    """The product cards on a listing page, new goods with a price, each once."""
    out: dict[str, Card] = {}
    for m in _CARD.finditer(html):
        whole = m.group(0)
        pid = _CARD_ID.search(whole)
        link = _CARD_LINK.search(whole)
        if not pid or not link:
            continue
        path = link.group(1).split("?", 1)[0].strip("/")
        slug = path.rsplit("/", 1)[-1]
        name = _text(link.group(2))
        if not slug or not name or _USED_NAME.search(name) or path.split("/", 1)[0] in USED_CATEGORIES:
            continue
        price_m = _CARD_PRICE.search(whole)
        selling = _money(price_m.group(1)) if price_m else None
        if selling is None:
            continue  # "Liên hệ", or not sold now
        last_m = _CARD_LAST.search(whole)
        listed = _money(last_m.group(1)) if last_m else None
        price, sale = (listed, selling) if listed is not None and listed > selling else (selling, None)
        img = _CARD_IMAGE.search(whole)
        image = html_lib.unescape((img.group(1) or img.group(2)) if img else "").strip()
        it = Item(
            sku=sku_of(slug),
            item_no=pid.group(1),
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
        )
        out.setdefault(it.sku, Card(pid.group(1), it))
    return list(out.values())


@dataclass(slots=True)
class Category:
    segment: str
    name: str
    items: list[Item]
    whole: bool  # every page read answered: may retire what it no longer lists


class HoangHaClient:
    def __init__(
        self, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 1.0
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL, timeout=httpx.Timeout(60, connect=20), follow_redirects=True,
        )
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> HoangHaClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> str | None:
        """The page's text; None when there is no such page."""
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
        if r.status_code in (404, 410):
            return None
        if r.status_code != 200:
            raise CatalogError(f"{path}: HTTP {r.status_code}")
        return r.text

    async def candidates(self) -> list[str]:
        sitemap = await self._get("/sitemap.xml")
        found = candidates(sitemap or "")
        if not found:
            raise CatalogError("no pages in the sitemap")
        return found

    async def _pages(self, path: str, first: str, menu: str, brand: str | None) -> tuple[list[Item], bool]:
        """A listing over all its pages, from its first page already read."""
        out: dict[str, Item] = {}
        html: str | None = first
        for page in range(1, MAX_PAGES + 1):
            if page > 1:
                html = await self._get(f"/{path}?p={page}")
                if html is None:
                    return list(out.values()), False
            cards = parse_cards(html or "", menu, brand)
            new = [c.item for c in cards if c.item.sku not in out]
            for it in new:
                out[it.sku] = it
            if not new and (page > 1 or not cards):
                break
        return list(out.values()), True

    async def category(self, segment: str) -> Category | None:
        """A category's products, branded from its brand pages; None when the
        candidate is no category page."""
        first = await self._get(f"/{segment}")
        if first is None or not is_category(first):
            return None
        name = category_name(first) or segment
        out: dict[str, Item] = {}
        whole = True
        for path, brand in brand_pages(first, segment):
            page1 = await self._get(f"/{path}")
            if page1 is None:
                continue
            items, ok = await self._pages(path, page1, name, brand)
            whole = whole and ok
            for it in items:
                out.setdefault(it.sku, it)
        items, ok = await self._pages(segment, first, name, None)
        whole = whole and ok
        for it in items:
            if it.sku in out:
                # The brand page's copy, with today's price from either.
                out[it.sku] = replace(out[it.sku], price=it.price, sale_price=it.sale_price)
            else:
                out[it.sku] = it
        return Category(segment, name, list(out.values()), whole)
