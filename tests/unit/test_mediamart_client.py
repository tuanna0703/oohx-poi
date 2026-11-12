"""MediaMartClient against a fake mediamart.vn: sitemaps, category pages, listings."""

from __future__ import annotations

import json

import httpx
import pytest

from poi_lake.catalog.chains import CHAINS
from poi_lake.catalog.mediamart import (
    BASE_URL,
    PAGE_SIZE,
    MediaMartClient,
    candidates,
    category_page,
    parse_cards,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _card(pid: str, path: str, name: str, price: str = "24.990.000 ₫", regular: str = "") -> str:
    """A card as the listing API returns it (quoted attributes, spread out)."""
    reg = (
        f'<p class="product-price-regular">\n<span>{regular}</span>\n'
        '<span class="product-price-saving">-3%</span>\n</p>'
        if regular else ""
    )
    return (
        '<div class="col-6 col-md-3 col-lg-3"><div class="card mb-4">\n'
        f'<a href="/{path}" data-id="{pid}" class="product-item">\n'
        f'<div class="card-img-top "><img src="https://cdn.mediamart.vn/images/product/{pid}.webp"\n'
        f' data-src="https://cdn.mediamart.vn/images/product/{pid}.jpg" alt="{name}" loading="lazy" /></div>\n'
        f'<div class="card-body"><p class="card-title product-name">\n{name}                </p>\n'
        '<ul class="list-inline product-attributes"><li>256 GB</li></ul>\n'
        f'<p class="card-text product-price">\n    {price}\n</p>\n{reg}\n'
        "</div></a></div></div>"
    )


def _category(cid: int, slug: str, name: str, brands: tuple[tuple[int, str], ...] = ()) -> str:
    """A category page as served (minified, unquoted attributes)."""
    bar = "".join(
        f'<li><a class="pa-filter pa-filter-fast pa-filter-brand pa-filter-brand-{bid}" '
        f"href={slug}-{label.lower()} data-href={label.lower()} data-id={bid} data-exist=0> "
        f"<label class=checkbox> <input type=checkbox> "
        f"<img src=https://cdn.mediamart.vn/images/brand/{bid}.png alt={label}> </label> </a>"
        for bid, label in brands
    )
    opts = {"key": None, "id": cid, "slugId": slug, "b": None, "page": 1, "pageSize": 38, "sort": None}
    return (
        f"<html><body><div class=pl-title><h1>{name}</h1></div>"
        f"<ul class=list-unstyled>{bar}</ul>"
        f"<script>var currentSearchOption={json.dumps(opts)};</script></body></html>"
    )


SITEMAP = f"""<urlset>
<url><loc>{BASE_URL}/smartphones/apple-iphone-17-256gb-den</loc></url>
<url><loc>{BASE_URL}/tu-lanh/tu-lanh-lg-inverter-335l</loc></url>
<url><loc>{BASE_URL}/apple-iphone-air-256gb-den-khong-gian-</loc></url>
<url><loc>{BASE_URL}/smartphones/samsung-galaxy-a08-4gb-64gb</loc></url>
</urlset>"""


def test_candidates_are_first_segments_of_product_urls() -> None:
    assert candidates([SITEMAP, "<urlset/>"]) == ["smartphones", "tu-lanh"]


def test_category_page_id_name_and_brands() -> None:
    page = category_page(_category(47, "smartphones", "Smartphones", ((779, "Apple"), (4, "Samsung"))), "smartphones")
    assert page is not None
    assert (page.id, page.name, page.brands) == ("47", "Smartphones", [("779", "Apple"), ("4", "Samsung")])
    assert category_page("<html><h1>iPhone 17</h1></html>", "smartphones") is None, "a product page"


def test_cards_prices_and_skips() -> None:
    page = "".join((
        _card("28314", "smartphones/apple-iphone-17-256gb-den", "Apple iPhone 17 256GB (Đen)",
              "24.990.000 ₫", "25.990.000 ₫"),
        _card("28345", "apple-iphone-air-256gb-den-khong-gian-", "Apple iPhone Air 256GB (Đen Không Gian)",
              "26.990.000 ₫"),
        _card("1", "binh-thuy-dien/binh-thuy-dien-sharp-kp-31btv-cu", "Bình thủy điện Sharp KP-31BTV"),
        _card("2", "smartphones/may-x", "Máy X (cũ)"),
        _card("3", "smartphones/may-y", "Máy Y", "Liên hệ"),
        _card("28314", "smartphones/apple-iphone-17-256gb-den", "Apple iPhone 17 256GB (Đen)"),
    ))
    cards = parse_cards(page, "Smartphones", "Apple")
    assert [c.product_id for c in cards] == ["28314", "28345", "1", "2", "3"], "each card once"
    items = [c.item for c in cards if c.item]
    assert [i.sku for i in items] == [
        "apple-iphone-17-256gb-den", "apple-iphone-air-256gb-den-khong-gian-", "binh-thuy-dien-sharp-kp-31btv-cu",
    ], "'-cu' in a slug is a model code; a name marked cũ and an unpriced card are left out"
    ip = items[0]
    assert (ip.price, ip.sale_price) == (25990000.0, 24990000.0), "the struck-through price is the list price"
    assert (ip.item_no, ip.brand, ip.menu, ip.seo_name, ip.name) == (
        "28314", "Apple", "Smartphones", "smartphones/apple-iphone-17-256gb-den", "Apple iPhone 17 256GB (Đen)",
    )
    assert ip.image_url == "https://cdn.mediamart.vn/images/product/28314.jpg", (
        "a URL, not the image: the uploaded picture, not the WebP rendition the CDN may lack"
    )
    assert (items[1].price, items[1].sale_price) == (26990000.0, None)


def test_the_chain_is_registered_online_only() -> None:
    c = CHAINS["mediamart"]
    assert c.online_only and c.products and c.brands == ("MediaMart",)


async def test_a_category_is_read_with_its_brand_filters() -> None:
    seen: list[str] = []
    ip = _card("28314", "smartphones/apple-iphone-17-256gb-den", "Apple iPhone 17 256GB (Đen)", "24.990.000 ₫")
    ss = _card("4001", "smartphones/samsung-galaxy-a08-4gb-64gb", "Samsung Galaxy A08 4GB/64GB", "2.990.000 ₫")
    zz = _card("9999", "smartphones/zphone-1", "ZPhone 1", "990.000 ₫")

    def handler(req: httpx.Request) -> httpx.Response:
        assert "poi-lake" in req.headers["user-agent"], "an honest user agent"
        if req.method == "POST":
            assert req.url.path == "/get-products"
            body = json.loads(req.content)
            assert body["id"] == "47" and body["pageSize"] == str(PAGE_SIZE)
            seen.append(f"POST b={body.get('b')} p={body['page']}")
            if body.get("b") == "779":
                return httpx.Response(200, text=ip)  # every page the same: the second brings nothing new
            if body["page"] == "1":
                return httpx.Response(200, text=ip + ss)
            if body["page"] == "2":
                return httpx.Response(200, text=ss + zz)
            return httpx.Response(200, text="")
        seen.append(req.url.path)
        if req.url.path.startswith("/sitemap_product_"):
            return httpx.Response(200, text=SITEMAP if req.url.path.endswith("_ph.xml") else "<urlset/>")
        if req.url.path == "/smartphones":
            return httpx.Response(200, text=_category(47, "smartphones", "Smartphones", ((779, "Apple"),)))
        return httpx.Response(404)

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    async with MediaMartClient(client=http, min_interval_s=0) as mm:
        segs = await mm.candidates()
        assert segs == ["smartphones", "tu-lanh"]
        cat = await mm.category(segs[0])
        assert cat is not None and cat.whole and cat.name == "Smartphones"
        by = {it.sku: it for it in cat.items}
        assert set(by) == {"apple-iphone-17-256gb-den", "samsung-galaxy-a08-4gb-64gb", "zphone-1"}
        assert by["apple-iphone-17-256gb-den"].brand == "Apple", "branded by its brand filter"
        assert by["samsung-galaxy-a08-4gb-64gb"].brand is None, "under no brand filter read"
        assert await mm.category(segs[1]) is None, "a 404 is no category"
    assert seen == [
        "/sitemap_product_ph.xml", "/sitemap_product_pm.xml", "/sitemap_product_pl.xml",
        "/smartphones", "POST b=779 p=1", "POST b=779 p=2",
        "POST b=None p=1", "POST b=None p=2", "POST b=None p=3",
        "/tu-lanh",
    ]
