"""CellphoneSClient against a fake cellphones.com.vn: sitemaps, product pages."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from poi_lake.catalog.cellphones import (
    BASE_URL,
    SKU_MAX,
    CellphoneSClient,
    GoneError,
    parse_product,
    product_slugs,
    sitemap_files,
    sku_of,
)
from poi_lake.catalog.chains import CHAINS

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _page(
    slug: str = "dien-thoai-samsung-galaxy-s26-ultra",
    price: str = "30490000",
    base: str | None = "40.990.000",
    availability: str = "InStock",
    crumbs: tuple[str, ...] = ("Điện thoại", "Samsung", "Galaxy S"),
    condition: str = "NewCondition",
    image: str = "https://cdn2.cellphones.com.vn/200x/media/catalog/product/s/a/s26.jpg",
) -> str:
    names = ["CELLPHONES", *crumbs, "Samsung Galaxy S26 Ultra 5G 12GB 256GB"]
    breadcrumb = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "item": {"@id": f"{BASE_URL}/x{i}.html", "name": n}}
            for i, n in enumerate(names)
        ],
    }
    product = {
        "@context": "https://schema.org/", "@type": "Product",
        "name": "Samsung  Galaxy S26 Ultra 5G 12GB 256GB", "image": image,
        "offers": {
            "@type": "Offer", "url": f"{BASE_URL}/{slug}.html", "priceCurrency": "VND", "price": price,
            "itemCondition": f"https://schema.org/{condition}",
            "availability": f"https://schema.org/{availability}",
        },
        "brand": {"@type": "Brand", "name": "Samsung"}, "sku": slug, "mpn": slug,
    }
    price_html = (
        f'<p class="sale-price">{int(price):,}đ</p>'.replace(",", ".")
        + (f'<p class="base-price">{base}đ</p>' if base else "")
    )
    return (
        "<html><head>"
        f'<script type="application/ld+json">{json.dumps(breadcrumb, ensure_ascii=False)}</script>'
        f'<script type="application/ld+json">{json.dumps(product, ensure_ascii=False)}</script>'
        f"</head><body>{price_html}</body></html>"
    )


INDEX = f"""<?xml version="1.0"?><sitemapindex>
<sitemap><loc>{BASE_URL}/sitemap/category-sitemap.xml</loc></sitemap>
<sitemap><loc>{BASE_URL}/sitemap/product-sitemap.xml</loc></sitemap>
<sitemap><loc>{BASE_URL}/sitemap/product-sitemap2.xml</loc></sitemap>
<sitemap><loc>{BASE_URL}/sitemap/page-sitemap.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP = f"""<urlset>
<url><loc>{BASE_URL}/dien-thoai-samsung-galaxy-s26-ultra.html</loc><lastmod>2026-11-09</lastmod></url>
<url><loc>{BASE_URL}/apple-macbook-air-13-m4-cu-tray-xuoc.html</loc></url>
<url><loc>{BASE_URL}/iphone-15-cu.html</loc></url>
<url><loc>{BASE_URL}/noi-chien-cuckoo-5l.html</loc></url>
<url><loc>{BASE_URL}/laptop/msi/bravo.html</loc></url>
<url><loc>{BASE_URL}/dien-thoai-samsung-galaxy-s26-ultra.html</loc></url>
</urlset>"""


def test_product_sitemaps_and_new_goods_only() -> None:
    assert sitemap_files(INDEX) == [
        f"{BASE_URL}/sitemap/product-sitemap.xml",
        f"{BASE_URL}/sitemap/product-sitemap2.xml",
    ]
    assert product_slugs(SITEMAP) == ["dien-thoai-samsung-galaxy-s26-ultra", "noi-chien-cuckoo-5l"], (
        "used goods and category pages left out; 'cuckoo' is not 'cũ'"
    )


def test_a_product_page_on_sale() -> None:
    it = parse_product(_page(), "dien-thoai-samsung-galaxy-s26-ultra")
    assert it is not None
    assert (it.sku, it.seo_name, it.brand) == (
        "dien-thoai-samsung-galaxy-s26-ultra", "dien-thoai-samsung-galaxy-s26-ultra", "Samsung",
    )
    assert it.name == "Samsung Galaxy S26 Ultra 5G 12GB 256GB"
    assert (it.price, it.sale_price) == (40990000.0, 30490000.0), "list price struck through, sells lower"
    assert it.category_path == ("Điện thoại", "Samsung", "Galaxy S") and it.menu == "Điện thoại"
    assert it.image_url and it.image_url.startswith("https://cdn2.cellphones.com.vn/"), "a URL, not the image"
    assert it.barcode is None


def test_prices_stock_and_used_goods() -> None:
    plain = parse_product(_page(base=None), "x")
    assert plain is not None and (plain.price, plain.sale_price) == (30490000.0, None)
    for page in (_page(price="0"), _page(availability="OutOfStock")):
        it = parse_product(page, "x")
        assert it is not None and it.price is None, "known, but not on sale now"
    assert parse_product(_page(crumbs=("Hàng cũ", "Điện thoại")), "x") is None
    assert parse_product(_page(condition="UsedCondition"), "x") is None
    assert parse_product("<html>no product</html>", "x") is None
    assert parse_product(_page(image="https://evil.example/x.png"), "x").image_url is None  # type: ignore[union-attr]
    assert parse_product(_page(crumbs=()), "x").menu == "Khác"  # type: ignore[union-attr]


def test_a_long_slug_is_shortened_stably() -> None:
    long = "laptop-" + "x" * 90
    assert len(sku_of(long)) == SKU_MAX and sku_of(long) == sku_of(long)
    assert sku_of(long) != sku_of(long + "y")
    assert sku_of("short") == "short"


def test_pages_read_longest_ago_come_after_new_ones() -> None:
    from poi_lake.workers.catalog import cellphones_order

    old, older = datetime(2026, 11, 8, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
    assert cellphones_order(["a", "b", "c", "d"], {"a": old, "c": older}) == ["b", "d", "c", "a"]


def test_the_chain_is_registered_online_only() -> None:
    c = CHAINS["cellphones"]
    assert c.online_only and c.products and c.brands == ("CellphoneS",)


async def test_client_reads_sitemaps_and_products() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        assert "poi-lake" in req.headers["user-agent"], "an honest user agent"
        if req.url.path == "/sitemap/sitemap_index.xml":
            return httpx.Response(200, text=INDEX)
        if req.url.path == "/sitemap/product-sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        if req.url.path == "/dien-thoai-samsung-galaxy-s26-ultra.html":
            return httpx.Response(200, text=_page())
        if req.url.path == "/noi-chien-cuckoo-5l.html":
            return httpx.Response(301, headers={"location": f"{BASE_URL}/do-gia-dung.html"})
        return httpx.Response(503)

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    async with CellphoneSClient(client=http, min_interval_s=0) as cp:
        slugs, whole = await cp.products()
        assert slugs == ["dien-thoai-samsung-galaxy-s26-ultra", "noi-chien-cuckoo-5l"]
        assert not whole, "product-sitemap2 failed: nothing may be retired for missing"
        it = await cp.product(slugs[0])
        assert it is not None and it.sale_price == 30490000.0
        with pytest.raises(GoneError):
            await cp.product(slugs[1])
    assert seen[:3] == ["/sitemap/sitemap_index.xml", "/sitemap/product-sitemap.xml", "/sitemap/product-sitemap2.xml"]
