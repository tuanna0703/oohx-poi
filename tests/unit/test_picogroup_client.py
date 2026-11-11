"""PicoGroupClient against a fake Pico / Nguyễn Kim: the catalogue API, pages, stores."""

from __future__ import annotations

import json

import httpx
import pytest

from poi_lake.catalog.chains import CHAINS, display_name
from poi_lake.catalog.picogroup import (
    SITES,
    GoneError,
    PicoGroupClient,
    page_brand,
    parse_product,
    parse_store,
)

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _product(code: str = "00003518", **kw: object) -> dict:
    d = {
        "_id": "64ee9159443659ddc4a69628", "code": code,
        "name": {"vi": "Máy sấy thông hơi Casper 7.2 Kg TD-72VWD"},
        "cat": {"_id": "64e5", "name": {"vi": "Máy sấy"}},
        "price": 6490000, "salePrice": 5099000, "finalPrice": 4499000, "stockStatus": "inStock",
        "gtin": "8859608901019",
        "slugs": {"value": {"vi": "may-say-thong-hoi-casper-72-kg-td-72vwd"}, "postfix": f"-{code}"},
        "thumbnail": "https://cdn.pico.vn/2023/09/06/1693.png",
    }
    d.update(kw)
    return d


def test_an_api_product() -> None:
    it = parse_product(_product(), "CASPER")
    assert it is not None
    assert (it.sku, it.item_no, it.barcode, it.brand) == ("00003518", "00003518", "8859608901019", "CASPER")
    assert (it.price, it.sale_price) == (6490000.0, 5099000.0), "list and selling price; flash sales left out"
    assert (it.menu, it.category_path) == ("Máy sấy", ("Máy sấy",))
    assert it.seo_name == "may-say-thong-hoi-casper-72-kg-td-72vwd-00003518"
    assert it.image_url == "https://cdn.pico.vn/2023/09/06/1693.png", "a URL, not the picture"


def test_stock_prices_and_skips() -> None:
    out = parse_product(_product(stockStatus="outOfStock"))
    assert out is not None and out.price is None, "known, not on sale now"
    same = parse_product(_product(price=5099000))
    assert same is not None and (same.price, same.sale_price) == (5099000.0, None)
    assert parse_product(_product(name={"vi": "Tủ lạnh LG (cũ)"})) is None, "used goods"
    assert parse_product(_product(code="")) is None
    assert parse_product(_product(cat={"name": {"vi": "BHMR"}})) is None, "a warranty package"
    assert parse_product(_product(cat={"name": {"vi": "KM_Gia dụng"}})) is None, "a gift"
    odd = parse_product(_product(cat=None, thumbnail="https://evil.example/x.png", gtin="n/a"))
    assert odd is not None and (odd.menu, odd.image_url, odd.barcode) == ("Khác", None, None)


def test_page_brand() -> None:
    ld = {"@context": "https://schema.org", "@type": "Product", "name": "X", "brand": {"@type": "Brand", "name": " CASPER "}}
    page = f'<script type="application/ld+json">{json.dumps(ld)}</script>'
    assert page_brand(page) == "CASPER"
    assert page_brand("<html></html>") is None


def test_pico_stores_get_the_brand_in_their_name() -> None:
    s = parse_store({
        "code": "NT", "name": {"vi": "76 Nguyễn Trãi - HN"}, "legalEntityCode": "PICO", "status": 1,
        "type": "center", "lat": 20.99, "lng": 105.81, "fullAddress": "Số 76 Nguyễn Trãi, Thanh Xuân, Hà Nội",
        "phoneNumber": "18006800", "provinceCode": "01",
    })
    assert s is not None and (s.code, s.group_code, s.phone) == ("NT", "PICO", "18006800")
    assert display_name("pico", s.name, s.address, s.store_type) == "Điện máy Pico 76 Nguyễn Trãi"
    assert parse_store({"code": "X", "lat": 0, "lng": 0}) is None, "no place in Vietnam"


def test_the_chains_are_registered() -> None:
    assert CHAINS["pico"].products and not CHAINS["pico"].online_only, "Pico has a store list"
    assert CHAINS["nguyenkim"].online_only and CHAINS["nguyenkim"].brands == ("Nguyen Kim",)


async def test_client_reads_the_assortment_brands_and_stores() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert "poi-lake" in req.headers["user-agent"], "an honest user agent"
        seen.append(f"{req.url.host}{req.url.path}?{req.url.query.decode()}")
        if req.url.host == "catalog.pico.vn":
            assert req.headers["x-legal-entity-code"] == "NK", "the chain's own assortment"
            page = int(req.url.params["page"])
            rows = [_product("00003518"), _product("A1")] if page == 1 else [_product("A1"), _product("B2")]
            return httpx.Response(200, json={"data": {"pages": 2, "total": 3, "page": page, "data": rows}})
        if req.url.host == "www.nguyenkim.com":
            if req.url.path.endswith("-B2"):
                return httpx.Response(404)
            ld = {"@type": "Product", "brand": {"name": "CASPER"}}
            return httpx.Response(200, text=f'<script type="application/ld+json">{json.dumps(ld)}</script>')
        if req.url.host == "ecommerce.pico.vn":
            return httpx.Response(200, json={"data": [
                {"code": "SG01", "legalEntityCode": "NK", "status": 1, "lat": 10.7, "lng": 106.6},
                {"code": "XT", "name": {"vi": "173 Xuân Thủy - HN"}, "legalEntityCode": "PICO", "status": 1,
                 "lat": 21.03, "lng": 105.78},
            ]})
        return httpx.Response(500)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with PicoGroupClient(SITES["nguyenkim"], client=http, min_interval_s=0) as pg:
        rows, whole = await pg.products()
        assert [r["code"] for r in rows] == ["00003518", "A1", "B2"] and whole
        assert await pg.brand("may-say-00003518") == "CASPER"
        with pytest.raises(GoneError):
            await pg.brand("x-B2")
    async with PicoGroupClient(SITES["pico"], client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                               min_interval_s=0) as pg:
        assert [s.code for s in await pg.stores()] == ["XT"], "Pico's own locations only"
    assert seen[:2] == [
        "catalog.pico.vn/user/api/products?page=1&limit=100",
        "catalog.pico.vn/user/api/products?page=2&limit=100",
    ]
