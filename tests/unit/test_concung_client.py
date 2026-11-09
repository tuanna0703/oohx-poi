"""ConCungClient against a fake concung.com: sitemap, category pages, cut transfers."""

from __future__ import annotations

import json

import httpx
import pytest

from poi_lake.catalog.chains import CHAINS
from poi_lake.catalog.concung import (
    BASE_URL,
    ConCungClient,
    category_urls,
    complete,
    parse_category,
    parse_product,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _product(pid: str, price: str = "170000", real: str = "85000", **kw: str) -> dict:
    return {
        "id_product": pid, "product_name": f"Bánh  gạo Happy Bite {pid}", "reference": "5508000000003",
        "manufacture_name": "Happy Bites", "price": price, "real_price": real,
        "category_name": "Bánh ăn dặm",
        "category_name_erp": "Thực phẩm cho bé;Snack ăn dặm;Bánh gạo;Happy Bites",
        "url_link": f"https://concung.com/banh-an-dam/banh-gao-{pid}.html",
        "image_cover": "https://cdn1.concung.com/x.webp", **kw,
    }


def _page(*products: dict, whole: bool = True) -> str:
    body = "".join(
        f'<span class="item_cart"></span><script>var productpid_1_{p["id_product"]}='
        f"{json.dumps(p, ensure_ascii=False)};</script>"
        for p in products
    )
    return f"<html><body>{body}</body></html>" if whole else f"<html><body>{body}"


SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://concung.com</loc></url>
<url><loc>https://concung.com/sua-bot-1014.html</loc></url>
<url><loc>https://concung.com/sua-bot/sua-meiji-123.html</loc></url>
<url><loc>https://concung.com/bim-ta-101259.html</loc></url>
<url><loc>https://concung.com/sua-bot-1014.html</loc></url>
<url><loc>https://concung.com/dieu-khoan-su-dung.html</loc></url>
<url><loc>https://concung.com/ta-quan-1"""  # cut mid-entry


def test_category_urls_from_a_cut_sitemap() -> None:
    assert category_urls(SITEMAP) == [
        "https://concung.com/sua-bot-1014.html",
        "https://concung.com/bim-ta-101259.html",
    ], "categories once, in order; product pages, policy pages and the cut entry left out"


def test_parse_product_keeps_facts_only() -> None:
    it = parse_product(_product("72829"), "banh-an-dam-1026")
    assert it is not None
    assert (it.sku, it.item_no, it.brand) == ("72829", "5508000000003", "Happy Bites")
    assert it.name == "Bánh gạo Happy Bite 72829", "whitespace collapsed"
    assert (it.price, it.sale_price) == (170000.0, 85000.0)
    assert it.category_path == ("Thực phẩm cho bé", "Snack ăn dặm", "Bánh gạo", "Happy Bites")
    assert it.seo_name == "banh-an-dam/banh-gao-72829"
    assert it.barcode is None, "the ERP reference is not an EAN"
    assert it.image_url == "https://cdn1.concung.com/x.webp", "the CDN URL, not the image"
    assert parse_product(_product("1", image_cover="https://evil.example/x.png"), "m").image_url is None
    assert it.menu == "banh-an-dam-1026"


def test_parse_product_prices() -> None:
    same = parse_product(_product("1", price="99000", real="99000"), None)
    assert same is not None and (same.price, same.sale_price) == (99000.0, None), "not on sale"
    only_real = parse_product(_product("2", price="0", real="45000"), None)
    assert only_real is not None and (only_real.price, only_real.sale_price) == (45000.0, None)
    assert parse_product({"id_product": "", "product_name": "x"}, None) is None
    assert parse_product({"id_product": "3", "product_name": " "}, None) is None


def test_parse_category_dedupes_and_survives_a_cut_page() -> None:
    page = _page(_product("1"), _product("2"), _product("1"))
    items = parse_category(page, "m")
    assert [i.sku for i in items] == ["1", "2"]
    assert complete(page)
    cut = _page(_product("1"), whole=False) + '<script>var productpid_1_9={"id_product":"9","pro'
    assert [i.sku for i in parse_category(cut, "m")] == ["1"], "the half-read product is dropped"
    assert not complete(cut)


def test_the_chain_is_registered_online_only() -> None:
    c = CHAINS["concung"]
    assert c.online_only and c.products and c.brands == ("Con Cưng",)


async def test_client_reads_sitemap_and_categories() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        assert "poi-lake" in req.headers["user-agent"], "an honest user agent"
        if req.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        if req.url.path == "/sua-bot-1014.html":
            return httpx.Response(200, text=_page(_product("1"), _product("2")))
        return httpx.Response(503)

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    async with ConCungClient(client=http, min_interval_s=0) as cc:
        urls = await cc.categories()
        items, whole = await cc.category(urls[0])
        assert [i.sku for i in items] == ["1", "2"] and whole
        assert items[0].menu == "sua-bot-1014", "no <h1>: the slug"
        from poi_lake.catalog.base import CatalogTransientError

        with pytest.raises(CatalogTransientError):
            await cc.category(urls[1])
    assert seen == ["/sitemap.xml", "/sua-bot-1014.html", "/bim-ta-101259.html"]


def test_a_category_is_named_as_its_page_heads_it() -> None:
    from poi_lake.catalog.concung import category_name, parse_category

    page = '<h1 itemprop="name">  Sữa bột &amp; pha sẵn </h1>' + _page(_product("7"))
    assert category_name(page) == "Sữa bột & pha sẵn"
    assert parse_category(page, category_name(page))[0].menu == "Sữa bột & pha sẵn"
    assert category_name("<html><body>no heading</body></html>") is None


async def test_a_category_is_read_page_by_page_until_a_short_page() -> None:
    from poi_lake.catalog import concung

    full = [_product(str(i)) for i in range(concung.PAGE_SIZE)]
    rest = [_product(str(100 + i)) for i in range(3)]
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.params.get("p")
        seen.append(p or "")
        return httpx.Response(200, text=_page(*(rest if p == "2" else full)))

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    async with ConCungClient(client=http, min_interval_s=0) as cc:
        items, whole = await cc.category("https://concung.com/sua-bot-1014.html")
    assert whole and len(items) == concung.PAGE_SIZE + 3
    assert seen == ["", "2"], "page 2 is short: the last"


async def test_paging_stops_when_a_page_repeats_or_is_cut() -> None:
    from poi_lake.catalog import concung

    full = [_product(str(i)) for i in range(concung.PAGE_SIZE)]
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=_page(*full))  # the site ignores ?p=

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    async with ConCungClient(client=http, min_interval_s=0) as cc:
        items, whole = await cc.category("https://concung.com/sua-bot-1014.html")
    assert (len(items), whole, calls) == (concung.PAGE_SIZE, True, 2)

    def cut(req: httpx.Request) -> httpx.Response:
        p = req.url.params.get("p")
        return httpx.Response(200, text=_page(*full, whole=p != "2"))

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(cut))
    async with ConCungClient(client=http, min_interval_s=0) as cc:
        _, whole = await cc.category("https://concung.com/sua-bot-1014.html")
    assert not whole, "a cut page 2 keeps the category from retiring anything"
