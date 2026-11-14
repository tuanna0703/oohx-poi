"""HasakiClient against a fake hasaki.vn: sitemaps, product pages."""

from __future__ import annotations

import json

import httpx
import pytest

from poi_lake.catalog.chains import CHAINS
from poi_lake.catalog.hasaki import (
    BASE_URL,
    GoneError,
    HasakiClient,
    parse_product,
    product_slugs,
    sitemap_files,
    sku_of,
    valid_gtin,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")

SLUG = "kem-danh-rang-marvis-whitening-mint-lam-trang-rang-85ml-113073"
INDEX = """<?xml version="1.0"?><sitemapindex>
<sitemap><loc>https://hasaki.vn/sitemap/cham-soc-da-mat-c4.xml</loc></sitemap>
<sitemap><loc>https://hasaki.vn/sitemap/dich-vu-phong-kham-c1839.xml</loc></sitemap>
<sitemap><loc>https://hasaki.vn/sitemap/categories.xml</loc></sitemap>
<sitemap><loc>https://hasaki.vn/sitemap/canonical.xml</loc></sitemap>
<sitemap><loc>https://hasaki.vn/sitemap/product-off-web.xml</loc></sitemap>
</sitemapindex>"""
SITEMAP = f"""<urlset>
<url><loc>https://hasaki.vn/san-pham/{SLUG}.html</loc></url>
<url><loc>https://hasaki.vn/danh-muc/kem-danh-rang-c2025.html</loc></url>
<url><loc>https://hasaki.vn/san-pham/son-li-x-12.html</loc></url>
<url><loc>https://hasaki.vn/san-pham/{SLUG}.html</loc></url>
</urlset>"""


def _page(
    *, price: object = 199000, mpn: object = "8004395111718", availability: str = "in stock",
    name: str = "Kem Đánh Răng Marvis Màu Bạc Làm Trắng Răng 85ml", brand: str = "Marvis",
    image: str = "https://media.hcdn.vn/catalog/product/f/a/marvis-1732075625.jpg",
    crumbs: tuple[str, ...] = ("Sức Khỏe - Làm Đẹp", "Chăm Sóc Cá Nhân", "Kem Đánh Răng"),
) -> str:
    product = {
        "@context": "https://schema.org/", "@type": "Product", "name": name, "image": [image],
        "sku": "422215456", "mpn": mpn, "brand": {"@type": "Brand", "name": brand},
        "offers": {"@type": "Offer", "priceCurrency": "VND", "price": price,
                   "availability": "https://schema.org/InStock"},
    }
    items = [{"@type": "ListItem", "position": i + 1, "name": c, "item": f"{BASE_URL}/danh-muc/x-c{i}.html"}
             for i, c in enumerate(crumbs)]
    items.append({"@type": "ListItem", "position": len(items) + 1, "name": name})
    crumb = {"@type": "BreadcrumbList", "itemListElement": items}
    return (
        f'<meta name="price:amount" content="{price}"/>'
        f'<meta name="product:availability" content="{availability}"/>'
        f'<script type="application/ld+json">{json.dumps(product)}</script>'
        f'<script type="application/ld+json">{json.dumps(crumb)}</script>'
    )


def test_the_sitemaps_of_goods_on_sale() -> None:
    assert sitemap_files(INDEX) == [
        "https://hasaki.vn/sitemap/cham-soc-da-mat-c4.xml",
        "https://hasaki.vn/sitemap/canonical.xml",
    ], "no clinic services, no category list, no off-web products"
    assert product_slugs(SITEMAP) == [SLUG, "son-li-x-12"]
    assert sku_of(SLUG) == "113073", "the product number, which outlives the slug's words"


def test_a_product_page() -> None:
    it = parse_product(_page(), SLUG)
    assert it is not None
    assert (it.sku, it.item_no, it.seo_name) == ("113073", "422215456", SLUG)
    assert (it.name, it.brand, it.barcode) == ("Kem Đánh Răng Marvis Màu Bạc Làm Trắng Răng 85ml", "Marvis",
                                               "8004395111718")
    assert (it.price, it.sale_price) == (199000.0, None), "one price: the list price is drawn from /ajax/"
    assert it.category_path == ("Sức Khỏe - Làm Đẹp", "Chăm Sóc Cá Nhân", "Kem Đánh Răng")
    assert it.menu == "Chăm Sóc Cá Nhân"
    assert it.image_url == "https://media.hcdn.vn/catalog/product/f/a/marvis-1732075625.jpg", "a URL, not the image"


def test_stock_prices_barcodes_and_gifts() -> None:
    for page in (_page(availability="out of stock"), _page(price=0), _page(price=9)):
        it = parse_product(page, SLUG)
        assert it is not None and it.price is None, "known, but not on sale now (the JSON-LD says InStock anyway)"
    for bad in ("893456962064028230", "8004395111719", None, "n/a", "00000000"):
        it = parse_product(_page(mpn=bad), SLUG)
        assert it is not None and it.barcode is None, bad
    assert parse_product(_page(name="[GIFT] shu uemura cleansing oil 4ML sachet"), SLUG) is None
    assert parse_product(_page(brand="Quà Tặng"), SLUG) is None
    assert parse_product("<html>no product</html>", SLUG) is None
    assert parse_product(_page(image="https://media.hcdn.vn/catalog/product"), SLUG).image_url is None  # type: ignore[union-attr]
    assert parse_product(_page(crumbs=()), SLUG).menu == "Khác"  # type: ignore[union-attr]
    assert parse_product(_page(crumbs=("Thời Trang",)), SLUG).menu == "Thời Trang"  # type: ignore[union-attr]


def test_gtin_check_digits() -> None:
    assert valid_gtin("8004395111718") and valid_gtin("8858842084090") and valid_gtin("96385074")
    assert valid_gtin("036000291452"), "a UPC-A"
    assert not valid_gtin("8004395111710") and not valid_gtin("12345") and not valid_gtin("abc")


def test_the_chain_is_registered_online_only() -> None:
    c = CHAINS["hasaki"]
    assert c.online_only and c.products and c.brands == ("Hasaki",) and c.site == "hasaki.vn"


async def test_client_reads_sitemaps_and_products() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        assert "poi-lake" in req.headers["user-agent"], "an honest user agent"
        if req.url.path == "/sitemap.xml":
            return httpx.Response(200, text=INDEX)
        if req.url.path == "/sitemap/cham-soc-da-mat-c4.xml":
            return httpx.Response(200, text=SITEMAP)
        if req.url.path == f"/san-pham/{SLUG}.html":
            return httpx.Response(200, text=_page())
        if req.url.path == "/san-pham/son-li-x-12.html":
            return httpx.Response(404)
        return httpx.Response(503)

    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    async with HasakiClient(client=http, min_interval_s=0) as hk:
        slugs, whole = await hk.products()
        assert slugs == [SLUG, "son-li-x-12"]
        assert not whole, "canonical.xml failed: nothing may be retired for missing"
        it = await hk.product(SLUG)
        assert it is not None and it.barcode == "8004395111718"
        with pytest.raises(GoneError):
            await hk.product("son-li-x-12")
    assert seen[:3] == ["/sitemap.xml", "/sitemap/cham-soc-da-mat-c4.xml", "/sitemap/canonical.xml"]
