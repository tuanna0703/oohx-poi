"""WinMartClient against a fake winmart.vn API."""

from __future__ import annotations

import httpx
import pytest

from poi_lake.catalog.winmart import (
    BASE_URL,
    CatalogTransientError,
    WinMartClient,
    km_between,
    parse_item,
    parse_store,
    product_url,
)


def _store(code: str, lat: float, lng: float, chain: str = "VMP") -> dict:
    return {
        "storeCode": code, "storeName": f"WM+ HCM  {code}", "storeGroupCode": "1999",
        "officeAddress": "72 Lê Thánh Tôn", "latitude": lat, "longitude": lng,
        "provinceCode": "HCM", "chainId": chain,
    }


def _client(handler) -> WinMartClient:
    http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handler))
    return WinMartClient(client=http, min_interval_s=0)


def test_parse_store_and_item() -> None:
    s = parse_store(_store("1545", 10.7778, 106.7021, "VMT"))
    assert s is not None and s.code == "1545" and s.store_type == "VMT"
    assert s.name == "WM+ HCM 1545", "whitespace collapsed"
    assert parse_store({**_store("x", 0, 0)}) is None, "outside Vietnam"
    assert parse_store({"storeName": "no code"}) is None

    it = parse_item(
        {"sku": "10196814G1", "itemNo": "10196814", "name": "Sữa tươi  Meiji 946ml",
         "brandName": "MEIJI", "barcode": "8850329003624", "uomName": "Gói",
         "price": 92000.0, "salePrice": 85000.0, "mch1Name": "Thực phẩm",
         "mch2Name": "Bơ, sữa, trứng", "mch3Name": "", "seoName": "meiji--s10196814",
         "mediaUrl": "", "mediaItems": [{"mediaUrl": "https://cdn.example/meiji.png"}]},
        "Sữa các loại",
    )
    assert it is not None
    assert it.name == "Sữa tươi Meiji 946ml"
    assert it.category_path == ("Thực phẩm", "Bơ, sữa, trứng")
    assert (it.price, it.sale_price, it.menu) == (92000.0, 85000.0, "Sữa các loại")
    assert it.image_url == "https://cdn.example/meiji.png"
    assert parse_item({"sku": "1", "name": "x", "mediaUrl": "http://plain.example/a.png"}, None).image_url is None
    assert parse_item({"sku": "", "name": "x"}, None) is None
    assert product_url("a--s1", "2AC9", "1999") == (
        "https://winmart.vn/products/a--s1?storeGroupCode=1999&storeCode=2AC9"
    )


@pytest.mark.asyncio
async def test_sweep_follows_stores_beyond_the_first_answer() -> None:
    # Three towns ~15 km apart: a seed only sees its own town, the sweep walks on.
    towns = {
        "A": [_store("a1", 10.00, 106.00), _store("a2", 10.01, 106.00)],
        "B": [_store("b1", 10.00, 106.14)],
        "C": [_store("c1", 10.00, 106.28)],
    }
    calls: list[tuple[float, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("store-by-chain"):
            return httpx.Response(200, json={"data": {"winMartStores": []}, "paging": {"totalCount": 4}})
        lat, lng = float(request.url.params["latitude"]), float(request.url.params["longitude"])
        calls.append((lat, lng))
        if lat > 10.5:
            return httpx.Response(500, json={"message": "lỗi"})
        rows = [s for t in towns.values() for s in t
                if km_between((lat, lng), (s["latitude"], s["longitude"])) < 16]
        return httpx.Response(200, json={"data": {"winMartStores": rows}})

    async with _client(handler) as wm:
        assert await wm.store_total() == 4
        found, stats = await wm.sweep_stores([(10.0, 106.0), (11.0, 106.0)])
    assert set(found) == {"a1", "a2", "b1", "c1"}
    assert stats == {"queries": 4, "failed_points": 1, "stores": 4}
    # a2 is 1.1 km from the seed: covered, never queried itself.
    assert (10.01, 106.0) not in calls


@pytest.mark.asyncio
async def test_store_pages_and_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.params
        if request.url.path.endswith("store-by-lat-long"):
            page = int(p["pageNumber"])
            n = 200 if page == 1 else 3
            rows = [_store(f"s{page}-{i}", 10.0, 106.0) for i in range(n)]
            return httpx.Response(200, json={"data": {"winMartStores": rows}})
        if request.url.path.endswith("/mt/api/web/v1/category"):
            return httpx.Response(200, json={"data": [
                {"parent": {"seoName": "gia-sieu-re--c114", "name": "Giá Siêu Rẻ"}},
                {"parent": {"seoName": "sua--c08", "name": "Sữa"}},
                {"parent": {"seoName": "bia--c31", "name": "Bia"}},
            ]})
        if p["slug"] == "bia--c31":
            return httpx.Response(500, json={"message": "lỗi"})
        page = int(p["pageNumber"])
        rows = [{"sku": f"sku{page}-{i}", "name": f"Sữa {i}", "price": 1000}
                for i in range(500 if page == 1 else 2)]
        if page == 1:
            rows.append(dict(rows[0]))  # same sku twice in one answer
        return httpx.Response(200, json={"data": {"items": rows}, "paging": {"totalCount": 502}})

    async with _client(handler) as wm:
        stores = await wm.stores_near(10.0, 106.0)
        menus = await wm.menus()
        items, failed = await wm.store_items("2AC9", "1999", menus)
    assert stores is not None and len(stores) == 203
    assert menus == [("sua--c08", "Sữa"), ("bia--c31", "Bia")]
    assert len(items) == 502 and failed == ["Bia"]


@pytest.mark.asyncio
async def test_transient_errors_raise() -> None:
    async with _client(lambda r: httpx.Response(503)) as wm:
        with pytest.raises(CatalogTransientError):
            await wm.store_total()
