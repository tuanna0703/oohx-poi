"""HoangHaClient against a fake hoanghamobile.com: sitemap, categories, brand pages."""

from __future__ import annotations

import httpx
import pytest

from poi_lake.catalog.chains import CHAINS
from poi_lake.catalog.hoangha import (
    BASE_URL,
    HoangHaClient,
    brand_pages,
    candidates,
    category_name,
    parse_cards,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _card(pid: str, slug: str, name: str, price: str = "5.090.000&#x111;", last: str = "") -> str:
    return (
        f'<article class="relative flex" data-category-product data-product-link-prefix="" data-product-id="{pid}">'
        f'<a class="group block" href="/dien-thoai/{slug}" data-product-detail-link aria-label="{name}">'
        f'<img class="size-full" alt="{name}" data-product-image src="https://i.hoanghamobile.vn/Uploads/{slug}.png;p=thumb-md" width="150"></a>'
        f'<a class="line-clamp-2" href="/dien-thoai/{slug}" data-product-detail-link>{name}</a>'
        f'<p class="m-0 font-bold" data-product-price> {price} </p>'
        f'<p class="m-0 line-through{" hidden" if not last else ""}" data-product-last-price> {last} </p>'
        '<p data-product-best-member-price> 1.000.000&#x111; </p>'
        "</article>"
    )


def _listing(*cards: str, brands: tuple[tuple[str, str], ...] = (), crumb: str = "Điện thoại") -> str:
    bar = "".join(
        f'<a href="/dien-thoai-di-dong/{sub}" class="x"><img src="/b.png" alt="{label}"></a>' for sub, label in brands
    )
    return (
        "<html><head><script type=\"application/ld+json\">"
        '{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":['
        '{"@type":"ListItem","position":1,"name":"Trang Chủ","item":"https://hoanghamobile.com/"},'
        f'{{"@type":"ListItem","position":2,"name":"{crumb}","item":"https://hoanghamobile.com/dien-thoai-di-dong"}}]}}'
        "</script></head><body><h1>Điện thoại chính hãng</h1>"
        '<div data-category-listing-host><section data-category-listing data-filters="">'
        f'<div class="swiper" data-category-brand-slider><div class="swiper-wrapper">{bar}</div>'
        '<div class="swiper-button-next"></div></div>'
        f"{''.join(cards)}</section></div></body></html>"
    )


SITEMAP = """<urlset>
<url><loc>https://hoanghamobile.com/dien-thoai-di-dong</loc></url>
<url><loc>https://hoanghamobile.com/dien-thoai-di-dong/oppo</loc></url>
<url><loc>https://hoanghamobile.com/dien-thoai/oppo-a17k</loc></url>
<url><loc>https://hoanghamobile.com/kho-san-pham-cu/iphone-12-cu</loc></url>
<url><loc>https://hoanghamobile.com/392-cau-giay-ha-noi</loc></url>
<url><loc>https://hoanghamobile.com/dien-thoai-di-dong/iphone</loc></url>
</urlset>"""


def test_candidates_are_first_segments_of_two_segment_urls() -> None:
    assert candidates(SITEMAP) == ["dien-thoai-di-dong", "dien-thoai"], "used-goods store left out"


def test_cards_prices_and_used_goods() -> None:
    page = _listing(
        _card("8840", "nubia-air-5g", "Nubia Air 5G 8GB/256GB", "5.090.000&#x111;", "5.490.000&#x111;"),
        _card("8905", "iphone-18-pro-max", "iPhone 18 Pro Max 256GB", "41.490.000&#x111;"),
        _card("1", "iphone-12-cu", "iPhone 12 (Cũ đẹp)"),
        _card("2", "lien-he", "Máy X", "Liên hệ"),
        _card("8840", "nubia-air-5g", "Nubia Air 5G 8GB/256GB"),
    )
    cards = parse_cards(page, "Điện thoại", "Nubia")
    assert [c.item.sku for c in cards] == ["nubia-air-5g", "iphone-18-pro-max"], "used, unpriced, repeats left out"
    nubia = cards[0].item
    assert (nubia.price, nubia.sale_price) == (5490000.0, 5090000.0), "the struck-through price is the list price"
    assert (nubia.item_no, nubia.brand, nubia.menu, nubia.seo_name) == (
        "8840", "Nubia", "Điện thoại", "dien-thoai/nubia-air-5g",
    )
    assert nubia.image_url == "https://i.hoanghamobile.vn/Uploads/nubia-air-5g.png;p=thumb-md"
    assert (cards[1].item.price, cards[1].item.sale_price) == (41490000.0, None)


def test_category_name_and_brand_bar() -> None:
    page = _listing(brands=(("iphone", "Apple"), ("oppo", "OPPO")))
    assert category_name(page) == "Điện thoại", "the breadcrumb, not the longer heading"
    assert brand_pages(page, "dien-thoai-di-dong") == [
        ("dien-thoai-di-dong/iphone", "Apple"), ("dien-thoai-di-dong/oppo", "OPPO"),
    ]


def test_the_chain_is_registered_online_only() -> None:
    c = CHAINS["hoangha"]
    assert c.online_only and c.products and c.brands == ("Hoàng Hà Mobile",)


async def test_a_category_is_read_with_its_brand_pages() -> None:
    seen: list[str] = []
    oppo = _card("3645", "oppo-a17k", "OPPO A17K (3GB/64GB)", "2.490.000&#x111;", "3.890.000&#x111;")
    iphone = _card("8905", "iphone-18-pro-max", "iPhone 18 Pro Max 256GB", "41.490.000&#x111;")
    other = _card("9999", "xphone-1", "XPhone 1", "990.000&#x111;")

    def handler(req: httpx.Request) -> httpx.Response:
        path, p = req.url.path, req.url.params.get("p")
        seen.append(path + (f"?p={p}" if p else ""))
        assert "poi-lake" in req.headers["user-agent"], "an honest user agent"
        if path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        if path == "/dien-thoai-di-dong":
            cards = (iphone, oppo) if not p else (other,) if p == "2" else ()
            return httpx.Response(200, text=_listing(*cards, brands=(("oppo", "OPPO"),)))
        if path == "/dien-thoai-di-dong/oppo":
            return httpx.Response(200, text=_listing(*((oppo,) if not p else ())))
        return httpx.Response(404)

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    async with HoangHaClient(client=http, min_interval_s=0) as hh:
        segs = await hh.candidates()
        cat = await hh.category(segs[0])
        assert cat is not None and cat.whole and cat.name == "Điện thoại"
        by = {it.sku: it for it in cat.items}
        assert set(by) == {"oppo-a17k", "iphone-18-pro-max", "xphone-1"}
        assert by["oppo-a17k"].brand == "OPPO", "branded by its brand page"
        assert by["iphone-18-pro-max"].brand is None, "under no brand page read"
        assert await hh.category(segs[1]) is None, "a 404 is no category"
    assert seen == [
        "/sitemap.xml",
        "/dien-thoai-di-dong", "/dien-thoai-di-dong/oppo", "/dien-thoai-di-dong/oppo?p=2",
        "/dien-thoai-di-dong?p=2", "/dien-thoai-di-dong?p=3",
        "/dien-thoai",
    ]
