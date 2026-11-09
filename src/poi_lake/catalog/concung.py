"""Con Cưng's online prices, from concung.com's public category pages.

There is no store-level data: concung.com sells online at one price, so the
chain's products are listed at one virtual store, ``ONLINE_STORE`` (type
``ONLINE``, never matched to a master). Its price history and daily summary
then work as for a chain's real stores.

What we read, and only that:

  * ``/sitemap.xml`` — the category pages (``/<slug>-<id>.html``). The file is
    large and the site often cuts long transfers, so a truncated sitemap still
    yields the categories read before the cut.
  * each category page — its name as the page heads it (``<h1>``, "Sữa bột
    pha sẵn"; the URL slug when there is none), kept as the products' menu,
    and its products, ``PAGE_SIZE`` a page, embedded in the page as
    ``var productpid_<page>_<id> = {...}``: id, ERP reference, name, brand,
    list price, selling price, ERP category, image. Later pages are the same
    page with ``?p=2``, ``?p=3`` … (what the site's own pager links to), read
    until a page is short, empty or brings nothing new.

robots.txt allows these pages (it disallows ``/search/``, ``tab=``, ``back=``,
customer pages). We keep facts: ids, names, brands, categories, prices, and
the URL of the product's image on Con Cưng's CDN, which bizmap links to (the
operator's decision, 2026-11-09, knowing article 15 of the site's terms); the
image itself is never copied. No descriptions or reviews. The ERP reference
(``5508000000003``) is Con Cưng's own code, not an EAN, and so is the
product page's "gtin": ``barcode`` stays empty.

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

CHAIN = "concung"
BASE_URL = "https://concung.com"
ONLINE_STORE = "online"
USER_AGENT = "poi-lake/0.1 (bizmap.live; price reference)"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xml"}  # sent on every request

# A category page: one path segment ending in "-<digits>.html". A product page
# has two segments (category/product-<id>.html).
CATEGORY_URL = re.compile(r"^https://concung\.com/([a-z0-9-]+-\d+)\.html$")
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_IMAGE = re.compile(r"^https://cdn\d*\.concung\.com/\S+$")
PAGE_SIZE = 60  # products a category page holds; a shorter page is the last
MAX_PAGES = 40
_H1 = re.compile(r"<h1[^>]*>\s*([^<]+?)\s*</h1>", re.S)
_PRODUCT_VAR = re.compile(r"var productpid_\d+_\d+\s*=\s*(\{.*?\});\s*</script>", re.S)


def category_urls(sitemap: str) -> list[str]:
    """The category pages a (possibly truncated) sitemap lists, in its order."""
    seen: dict[str, None] = {}
    for loc in _LOC.findall(sitemap):
        if CATEGORY_URL.match(loc):
            seen.setdefault(loc, None)
    return list(seen)


def _money(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def parse_product(d: dict[str, Any], menu: str | None) -> Item | None:
    """One embedded product. ``price`` is the list price; ``real_price`` what
    it sells at, kept as the sale price when lower."""
    sku = str(d.get("id_product") or "").strip()
    name = " ".join(str(d.get("product_name") or "").split())
    if not sku or not name:
        return None
    price = _money(d.get("price"))
    real = _money(d.get("real_price"))
    if price is None:
        price, real = real, None
    sale = real if real is not None and price is not None and real < price else None
    path = tuple(p.strip() for p in str(d.get("category_name_erp") or "").split(";") if p.strip())
    if not path and d.get("category_name"):
        path = (str(d["category_name"]).strip(),)
    url = str(d.get("url_link") or "")
    seo = url.removeprefix(BASE_URL + "/").removesuffix(".html") or None
    brand = " ".join(str(d.get("manufacture_name") or "").split()) or None
    return Item(
        sku=sku,
        item_no=str(d.get("reference") or "").strip(),
        name=name,
        brand=brand,
        barcode=None,  # the reference is Con Cưng's ERP code, not an EAN
        uom=None,
        price=price,
        sale_price=sale,
        category_path=path,
        menu=menu,
        seo_name=seo,
        image_url=_image(d),
    )


def _image(d: dict[str, Any]) -> str | None:
    """The product's image on Con Cưng's CDN (a URL, never the image)."""
    for key in ("image_cover", "image"):
        url = str(d.get(key) or "").strip()
        if _IMAGE.match(url):
            return url
    return None


def parse_category(html: str, menu: str | None) -> list[Item]:
    """The products embedded in a category page, each once. A page cut off
    mid-transfer yields the products read before the cut."""
    out: dict[str, Item] = {}
    for raw in _PRODUCT_VAR.findall(html):
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        it = parse_product(d, menu)
        if it is not None:
            out.setdefault(it.sku, it)
    return list(out.values())


def category_name(html: str) -> str | None:
    """The category's name as the page heads it: "Sữa bột pha sẵn"."""
    m = _H1.search(html)
    name = " ".join(html_lib.unescape(m.group(1)).split()) if m else ""
    return name or None


def complete(html: str) -> bool:
    """Whether the page arrived whole: a cut page must not retire products."""
    return html.rstrip().endswith("</html>")


class ConCungClient:
    def __init__(
        self, *, client: httpx.AsyncClient | None = None, min_interval_s: float = 3.0
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(150, connect=20),
            follow_redirects=True,
        )
        self._min_interval_s = min_interval_s
        self._next_at = 0.0
        self.requests = 0

    async def __aenter__(self) -> ConCungClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _get(self, path: str) -> str:
        """The page's text; a body cut off mid-transfer comes back as far as it got."""
        wait = self._next_at - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self.requests += 1
        chunks: list[bytes] = []
        try:
            async with self._client.stream("GET", path, headers=HEADERS) as r:
                if r.status_code in (429, 502, 503, 504):
                    raise CatalogTransientError(f"{path}: HTTP {r.status_code}")
                if r.status_code != 200:
                    raise CatalogError(f"{path}: HTTP {r.status_code}")
                try:
                    async for chunk in r.aiter_bytes():
                        chunks.append(chunk)
                except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout) as exc:
                    if not chunks:
                        raise CatalogTransientError(f"{path}: {exc}") from exc
                    logger.info("concung %s cut after %d bytes", path, sum(map(len, chunks)))
        except httpx.TransportError as exc:
            raise CatalogTransientError(f"{path}: {exc}") from exc
        finally:
            self._next_at = time.monotonic() + self._min_interval_s
        return b"".join(chunks).decode("utf-8", errors="replace")

    async def categories(self) -> list[str]:
        """Category page URLs from the sitemap."""
        urls = category_urls(await self._get("/sitemap.xml"))
        if not urls:
            raise CatalogError("no category pages in the sitemap")
        return urls

    async def category(self, url: str) -> tuple[list[Item], bool]:
        """A category's products over all its pages, and whether every page
        read arrived whole. A cut page ends the category there."""
        m = CATEGORY_URL.match(url)
        if m is None:
            raise CatalogError(f"not a category page: {url}")
        slug = m.group(1)
        out: dict[str, Item] = {}
        name: str | None = None
        for page in range(1, MAX_PAGES + 1):
            html = await self._get(f"/{slug}.html" if page == 1 else f"/{slug}.html?p={page}")
            if name is None:
                name = category_name(html) or slug
            items = parse_category(html, name)
            new = [it for it in items if it.sku not in out]
            for it in new:
                out[it.sku] = it
            if not complete(html):
                return list(out.values()), False
            if len(items) < PAGE_SIZE or not new:
                break
        return list(out.values()), True
