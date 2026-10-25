"""BHXClient against a fake bachhoaxanh.com store API, and the chain helpers."""

from __future__ import annotations

import httpx
import pytest

from poi_lake.catalog.bachhoaxanh import BASE_URL, BHXClient, parse_store
from poi_lake.catalog.chains import display_name
from poi_lake.pipeline.extractors.chain_websites import ChainWebsitesExtractor
from poi_lake.pipeline.normalize.category import CategoryMapper
from poi_lake.services.catalog import street_key


def _store(sid: int, province: int = 1027, virtual: bool = False) -> dict:
    return {
        "storeId": sid, "lat": 10.79, "lng": 106.61, "provinceId": province,
        "storeAddress": f"BHX {sid} Dương Văn Dương, Phường Phú Thọ Hòa, Thành phố Hồ Chí Minh, Việt Nam",
        "openHour": "5 giờ 30 phút -  21 giờ 30 phút", "isStoreVirtual": virtual,
    }


def test_parse_store() -> None:
    s = parse_store(_store(116))
    assert s is not None
    assert (s.code, s.name, s.region_code) == ("116", "BHX 116 Dương Văn Dương", "1027")
    assert s.open_hours == "5 giờ 30 phút - 21 giờ 30 phút"
    assert parse_store(_store(117, virtual=True)) is None
    assert parse_store({**_store(118), "lat": 0}) is None


@pytest.mark.asyncio
async def test_all_stores_pages_provinces_and_counts_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/GetFull"):
            return httpx.Response(200, json={"code": 0, "data": {"provinces": [
                {"id": 1027, "nameNew": "TP.HCM"}, {"id": 2, "name": "Hà Nội"}, {"id": 3, "name": "Lỗi"},
            ]}})
        p = request.url.params
        province, page = int(p["provinceId"]), int(p["pageIndex"])
        if province == 3:
            return httpx.Response(200, json={"code": 1, "data": None})
        if province == 2:
            return httpx.Response(200, json={"code": 0, "data": {"total": 1, "stores": [_store(900, 2)]}})
        # TP.HCM: 52 stores, one of them virtual, over two pages.
        rows = [_store(i, virtual=(i == 5)) for i in range(page * 50, min(52, page * 50 + 50))]
        return httpx.Response(200, json={"code": 0, "data": {"total": 52, "stores": rows}})

    transport = httpx.MockTransport(handler)
    async with BHXClient(client=httpx.AsyncClient(base_url=BASE_URL, transport=transport),
                         min_interval_s=0) as bhx:
        stores, stats = await bhx.all_stores()
    assert len(stores) == 52  # 51 physical in TP.HCM + 1 in Hà Nội
    assert "5" not in stores and "900" in stores
    assert stats == {"provinces": 3, "failed_provinces": 1, "published_total": 53, "stores": 52}


def test_street_key() -> None:
    assert street_key("Số 12 Đường Lê Lợi, Phường Bến Thành") == "12 le loi"
    assert street_key("BHX 12 Lê Lợi, Phường Bến Thành") == "12 le loi"
    assert street_key("30 Đường Đô Đốc Bảo, Tổ 60") == "30 do doc bao"
    assert street_key("193-195 Dương Văn Dương, P. Phú Thọ Hòa") == "193 195 van"
    assert street_key("12 Dương Bá Trạc") == street_key("Số 12 Đường Dương Bá Trạc") == "12 ba trac"
    assert street_key("Chung cư Bàu Cát II - Đường Vườn Lan, P. 10") is None
    assert street_key("") is None


def test_display_name_puts_the_brand_first() -> None:
    assert display_name("bachhoaxanh", "BHX 12 Lê Lợi", "BHX 12 Lê Lợi, P. Bến Thành", "BHX") == \
        "Bách hoá XANH 12 Lê Lợi"
    assert display_name("winmart", "WM+ HCM 1.01", "72 Lê Thánh Tôn, P. Bến Nghé", "VMP") == \
        "WinMart+ 72 Lê Thánh Tôn"
    assert display_name("winmart", "WM HCM Bàu Cát", "Chung cư Bàu Cát II, P. 10", "VMT") == \
        "WinMart Chung cư Bàu Cát II"


def test_chain_websites_extractor_and_category() -> None:
    fields = ChainWebsitesExtractor().extract({
        "name": "Bách hoá XANH 12 Lê Lợi", "address": "BHX 12 Lê Lợi, P. Bến Thành",
        "phone": None, "category": "retail|retail.grocery", "lat": 10.77, "lng": 106.7,
    })
    assert fields is not None and fields.location == (10.77, 106.7)
    m = CategoryMapper()
    assert m.map("chain_websites", fields.raw_category) == ("retail", "retail.grocery")
    assert m.map("chain_websites", "retail|hospitality.cafes") == (None, None)
    assert ChainWebsitesExtractor().extract({"name": ""}) is None
