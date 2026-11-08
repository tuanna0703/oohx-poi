"""Chain catalog: store list upsert, store↔master matching, product listing API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from poi_lake.catalog.winmart import Item, Store
from poi_lake.db import get_sessionmaker
from poi_lake.services import catalog
from tests.integration.test_master_pois_api import _cleanup, _http, _make_client, _seed_master

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _store(chain: str, code: str, lat: float, lng: float) -> Store:
    return Store(code=code, name=f"WM+ {code}", store_type="VMP", group_code="1999",
                 address="Q.1", lat=lat, lng=lng, region_code="HCM", raw={"storeCode": code})


def _item(sku: str, name: str, menu: str, price: float = 10000, sale: float | None = None) -> Item:
    return Item(sku=sku, item_no=sku, name=name, brand="Vinamilk", barcode="893" + sku,
                uom="Hộp", price=price, sale_price=sale if sale is not None else price,
                category_path=("Thực phẩm", "Sữa"), menu=menu, seo_name=f"{sku}--s1",
                image_url=f"https://cdn.example/{sku}.png")


async def _drop_chain(s, chain: str) -> None:
    await s.execute(text("DELETE FROM chain_price_changes WHERE chain = :c"), {"c": chain})
    await s.execute(text("DELETE FROM chain_price_daily WHERE chain = :c"), {"c": chain})
    await s.execute(text("DELETE FROM chain_store_products WHERE chain = :c"), {"c": chain})
    await s.execute(text("DELETE FROM chain_products WHERE chain = :c"), {"c": chain})
    await s.execute(text("DELETE FROM chain_stores WHERE chain = :c"), {"c": chain})
    await s.commit()


async def test_stores_match_masters_and_list_products() -> None:
    sm = get_sessionmaker()
    tag = "cat-" + uuid.uuid4().hex[:6]
    chain, brand = f"test-{tag}", f"Brand {tag}"
    now = datetime.now(UTC)
    async with sm() as s:
        key, _ = await _make_client(s, tag)
        near = await _seed_master(s, name="WinMart+ A", lat=10.7700, lng=106.7000, test_tag=tag, brand=brand)
        # 2 stores 40 m apart (one mall), 1 master: only the closer store gets it.
        await _seed_master(s, name="WinMart+ far", lat=10.8000, lng=106.7000, test_tag=tag, brand=brand)
        stores = [
            _store(chain, "A", 10.7701, 106.7000),
            _store(chain, "A2", 10.7704, 106.7000),
            _store(chain, "B", 10.9000, 106.7000),
        ]
    try:
        async with sm() as s:
            saved = await catalog.save_stores(s, chain, stores, now=now, complete=True)
            assert saved == {"seen": 3, "new": 3, "retired": 0}
            assert await catalog.match_stores(s, chain, [brand]) == {"matched": 1, "matched_by_address": 0}
            link = (await s.execute(
                text("SELECT store_code, match_m FROM chain_stores WHERE chain = :c AND master_id IS NOT NULL"),
                {"c": chain},
            )).one()
            assert link.store_code == "A" and link.match_m < 20

            # A later complete sweep without B retires it; a partial one would not.
            later = now + timedelta(days=7)
            again = await catalog.save_stores(s, chain, stores[:2], now=later, complete=True)
            assert again == {"seen": 2, "new": 0, "retired": 1}
            assert await catalog.stores_to_crawl(s, chain, ["HCM"]) == ["A", "A2"]
            assert await catalog.stores_to_crawl(s, chain, ["HNI"]) == []

            items = [
                _item("1", "Sữa tươi Vinamilk 180ml", "Sữa các loại", 8000, 7000),
                _item("2", "Sữa đặc Ông Thọ", "Sữa các loại"),
                _item("3", "Bia Hà Nội 330ml", "Bia"),
            ]
            await catalog.save_items(s, chain, "A", items, answered_menus=["Sữa các loại", "Bia"], now=later)
            # Next crawl: item 2 gone, the Bia menu failed — its row stays.
            await catalog.save_items(
                s, chain, "A", items[:1], answered_menus=["Sữa các loại"], now=later + timedelta(hours=1)
            )
            skus = set((await s.execute(
                text("SELECT sku FROM chain_store_products WHERE chain = :c AND store_code = 'A'"), {"c": chain}
            )).scalars())
            assert skus == {"1", "3"}

        async with await _http() as c:
            r = await c.get(f"/api/v1/master-pois/{near}/products", params={"q": "sua tuoi"},
                            headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["store"]["store_code"] == "A" and body["store"]["items_count"] == 2
            assert body["menus"] == [{"name": "Bia", "count": 1}, {"name": "Sữa các loại", "count": 1}]
            assert body["total"] == 1
            item = body["items"][0]
            assert (item["name"], item["price"], item["sale_price"]) == ("Sữa tươi Vinamilk 180ml", 8000, 7000)
            assert item["url"].endswith("/products/1--s1?storeGroupCode=1999&storeCode=A")
            assert item["image_url"] == "https://cdn.example/1.png"

            r = await c.get(f"/api/v1/master-pois/{near}/products", params={"q": "100%_"},
                            headers={"X-API-Key": key})
            assert r.json()["total"] == 0, "LIKE wildcards are literal"

            r = await c.get(f"/api/v1/master-pois/{uuid.uuid4()}/products", headers={"X-API-Key": key})
            assert r.json()["store"] is None

            # The whole chain: one row per product, and which masters list what.
            r = await c.get(f"/api/v1/catalog/{chain}/products", headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            body = r.json()
            assert (body["stores"], body["total"]) == (1, 2)
            assert body["menus"] == [{"name": "Bia", "count": 1}, {"name": "Sữa các loại", "count": 1}]
            milk = next(i for i in body["items"] if i["sku"] == "1")
            assert (milk["stores"], milk["min_price"], milk["max_price"]) == (1, 7000, 7000)
            r = await c.get(f"/api/v1/catalog/{chain}/products", params={"q": "bia", "menu": "Bia"},
                            headers={"X-API-Key": key})
            assert [i["sku"] for i in r.json()["items"]] == ["3"]
            # A barcode (or its start) finds the product too; LIKE wildcards stay literal.
            r = await c.get(f"/api/v1/catalog/{chain}/products", params={"q": "8933"}, headers={"X-API-Key": key})
            assert [(i["sku"], i["barcode"]) for i in r.json()["items"]] == [("3", "8933")]
            r = await c.get(f"/api/v1/catalog/{chain}/products", params={"q": "100%_"}, headers={"X-API-Key": key})
            assert r.json()["total"] == 0
            r = await c.get(f"/api/v1/catalog/{chain}/stores", headers={"X-API-Key": key})
            assert r.json()["master_ids"] == [str(near)]
            r = await c.get(f"/api/v1/catalog/{chain}/stores", params={"sku": "2"}, headers={"X-API-Key": key})
            assert r.json()["master_ids"] == [], "sku 2 left store A's listing"
            r = await c.get("/api/v1/catalog/Bad Chain/stores", headers={"X-API-Key": key})
            assert r.status_code == 422

        # Price history: a change is kept, an unchanged price is not, a product
        # leaving the listing is noted as no price; the day's summary follows.
        async with sm() as s:
            await catalog.save_items(
                s, chain, "A", [_item("1", "Sữa tươi Vinamilk 180ml", "Sữa các loại", 8000, 6500)],
                answered_menus=["Sữa các loại"], now=later + timedelta(hours=2),
            )
            await catalog.save_items(
                s, chain, "A", [_item("1", "Sữa tươi Vinamilk 180ml", "Sữa các loại", 8000, 6500)],
                answered_menus=["Sữa các loại"], now=later + timedelta(hours=3),
            )
            hist = (await s.execute(
                text("SELECT sku, sale_price FROM chain_price_changes WHERE chain = :c ORDER BY sku, seen_at"),
                {"c": chain},
            )).all()
            assert [(h.sku, h.sale_price) for h in hist] == [
                ("1", 7000), ("1", 6500), ("2", 10000), ("2", None), ("3", 10000)
            ]
            today = datetime.now(UTC).date()
            assert await catalog.snapshot_prices(s, chain, today, later + timedelta(hours=4)) == 2
            assert await catalog.snapshot_prices(s, chain, today, later + timedelta(hours=5)) == 2, "upsert"

        async with await _http() as c:
            r = await c.get(f"/api/v1/catalog/{chain}/products/1/prices", headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            day = r.json()["days"][-1]
            assert (day["stores"], day["on_sale"], day["mode_price"], day["regular_price"]) == (1, 1, 6500, 8000)
            r = await c.get(f"/api/v1/catalog/{chain}/products/nope/prices", headers={"X-API-Key": key})
            assert r.status_code == 404
            r_json = (await c.get(f"/api/v1/catalog/{chain}/products/1/prices",
                                   headers={"X-API-Key": key})).json()
            assert r_json["today"]["price"] == 6500 and r_json["today"]["stores"] == 1
            assert r_json["product"]["owner"]["company"] == "Vinamilk"

            # By brand and by company: VINAMILK is in brand_owners.csv.
            r = await c.get(f"/api/v1/catalog/{chain}/brands", headers={"X-API-Key": key})
            assert [(b["slug"], b["skus"]) for b in r.json()["brands"]] == [("vinamilk", 2)]
            r = await c.get(f"/api/v1/catalog/{chain}/brands/vinamilk", headers={"X-API-Key": key})
            body = r.json()
            assert (body["stores"], len(body["products"]), body["brand"]["company_slug"]) == (1, 2, "vinamilk")
            assert body["on_sale_skus"] == 1
            r = await c.get(f"/api/v1/catalog/{chain}/companies/vinamilk", headers={"X-API-Key": key})
            group = r.json()["groups"][0]
            assert (group["skus"], group["rivals"][0]["company"]) == (2, "Vinamilk")
            r = await c.get(f"/api/v1/catalog/{chain}/brands/nope", headers={"X-API-Key": key})
            assert r.status_code == 404
    finally:
        async with sm() as s:
            await _drop_chain(s, chain)
            await _cleanup(s, tag)


async def test_address_match_missing_stores_and_store_details() -> None:
    sm = get_sessionmaker()
    tag = "cat-" + uuid.uuid4().hex[:6]
    chain, brand = f"test-{tag}", f"Brand {tag}"
    now = datetime.now(UTC)

    def store(code: str, lat: float, address: str) -> Store:
        return Store(code=code, name=f"BHX {code}", store_type="BHX", group_code="", address=address,
                     lat=lat, lng=106.7, region_code="1027", raw={}, open_hours="6h - 22h")

    async with sm() as s:
        key, _ = await _make_client(s, tag)
        # ~400 m north of store P, same house number and street: one store, two pins.
        p_master = await _seed_master(s, name="BHX P", lat=10.7036, lng=106.7, test_tag=tag, brand=brand)
        # ~250 m from store Q, a different address: maybe Q with a misplaced pin.
        await _seed_master(s, name="BHX other", lat=10.80225, lng=106.7, test_tag=tag, brand=brand)
        await s.execute(
            text("UPDATE master_pois SET canonical_address = CASE id WHEN :p THEN :pa ELSE :oa END "
                 "WHERE archived_reason = :tag"),
            {"p": p_master, "pa": "Số 12 Đường Lê Lợi, Phường Bến Thành, TP.HCM",
             "oa": "88 Hai Bà Trưng, Phường Sài Gòn, TP.HCM", "tag": tag},
        )
        await s.commit()
    try:
        async with sm() as s:
            await catalog.save_stores(s, chain, [
                store("P", 10.7000, "BHX 12 Lê Lợi, Phường Bến Thành, TP.HCM"),
                store("Q", 10.8000, "BHX 5 Nguyễn Huệ, Phường Sài Gòn, TP.HCM"),
                store("R", 11.5000, "BHX 7 Trần Phú, Phường 1, TP. Vũng Tàu"),
            ], now=now, complete=True)
            assert await catalog.match_stores(s, chain, [brand]) == {"matched": 1, "matched_by_address": 1}

            missing = await catalog.missing_stores(s, chain, [brand])
            # Q has a master of the brand 250 m away, inside MISSING_MIN_M: not offered.
            assert [m["store_code"] for m in missing] == ["R"]
            assert missing[0]["nearest_m"] is None
            assert [m["store_code"] for m in await catalog.missing_stores(s, chain, [brand], min_m=150)] == ["Q", "R"]

        async with await _http() as c:
            r = await c.get(f"/api/v1/master-pois/{p_master}/chain-stores", headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            [only] = r.json()["stores"]
            assert (only["store_code"], only["open_hours"]) == ("P", "6h - 22h")
            assert 350 < only["match_m"] < 450
    finally:
        async with sm() as s:
            await _drop_chain(s, chain)
            await _cleanup(s, tag)


async def test_chain_websites_adapter_builds_a_raw_record() -> None:
    from poi_lake.adapters.base import AdapterConfig, AdapterError
    from poi_lake.adapters.chain_websites import ChainWebsitesAdapter

    sm = get_sessionmaker()
    code = "test-" + uuid.uuid4().hex[:8]
    async with sm() as s:
        await catalog.save_stores(s, "bachhoaxanh", [Store(
            code=code, name="BHX 12 Lê Lợi", store_type="BHX", group_code="",
            address="BHX 12 Lê Lợi, Phường Bến Thành, TP.HCM", lat=10.77, lng=106.70,
            region_code="1027", raw={}, open_hours="6h - 22h",
        )], now=datetime.now(UTC), complete=False)
    try:
        adapter = ChainWebsitesAdapter(AdapterConfig())
        rec = await adapter.fetch_by_id(f"bachhoaxanh:{code}")
        assert rec is not None and rec.location == pytest.approx((10.77, 106.70))
        assert rec.raw_payload["name"] == "Bách hoá XANH 12 Lê Lợi"
        assert rec.raw_payload["category"] == "retail|retail.grocery"
        assert await adapter.fetch_by_id("bachhoaxanh:nope") is None
        with pytest.raises(AdapterError):
            await adapter.fetch_by_id("nochain:1")
    finally:
        async with sm() as s:
            await s.execute(text("DELETE FROM chain_stores WHERE chain = 'bachhoaxanh' AND store_code = :c"),
                            {"c": code})
            await s.commit()


async def test_concung_online_prices_at_a_virtual_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Con Cưng pass: products at the virtual online store, listed by the
    products API; a cut page retires nothing; the store is never 'missing'."""
    import json as _json

    import httpx

    from poi_lake.catalog import concung
    from poi_lake.workers import catalog as catalog_worker

    def product(pid: str, price: str, real: str) -> dict:
        return {"id_product": pid, "product_name": f"Sữa test {pid}", "reference": f"55{pid}",
                "manufacture_name": "Meiji", "price": price, "real_price": real,
                "category_name_erp": "Sữa;Sữa bột", "url_link": f"https://concung.com/sua/s-{pid}.html"}

    def page(products: list[dict], whole: bool = True) -> str:
        body = "".join(f"<script>var productpid_1_{p['id_product']}={_json.dumps(p)};</script>" for p in products)
        return f"<html>{body}</html>" if whole else f"<html>{body}"

    pages = {
        "/sua-bot-1014.html": page([product("1", "200000", "180000"), product("2", "90000", "90000")]),
        "/bim-ta-1015.html": page([product("3", "300000", "300000")]),
    }
    sitemap = "<urlset>" + "".join(f"<url><loc>https://concung.com{p}</loc></url>" for p in pages) + "</urlset>"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap)
        return httpx.Response(200, text=pages[req.url.path])

    real_client = concung.ConCungClient

    def fake_client(**kw):  # type: ignore[no-untyped-def]
        http = httpx.AsyncClient(base_url=concung.BASE_URL, transport=httpx.MockTransport(handler))
        return real_client(client=http, min_interval_s=0)

    monkeypatch.setattr(concung, "ConCungClient", fake_client)
    sm = get_sessionmaker()
    tag = "cc-" + uuid.uuid4().hex[:6]
    async with sm() as s:
        key, _ = await _make_client(s, tag)
    try:
        first = await catalog_worker._refresh_concung()
        assert (first["categories"], first["whole"], first["items"]) == (2, 2, 3)

        # Next pass: product 2 left its (whole) page; the other page is cut and
        # has lost product 3 from what arrived, which must not retire it.
        pages["/sua-bot-1014.html"] = page([product("1", "200000", "150000")])
        pages["/bim-ta-1015.html"] = page([], whole=False)
        second = await catalog_worker._refresh_concung()
        assert (second["whole"], second["cut"], second["removed"]) == (1, 1, 1)

        async with sm() as s:
            store = (await s.execute(text(
                "SELECT store_type, master_id, ST_X(location::geometry) AS x FROM chain_stores "
                "WHERE chain = 'concung' AND store_code = 'online'"
            ))).one()
            assert (store.store_type, store.master_id, store.x) == ("ONLINE", None, 0.0)
            listed = dict((await s.execute(text(
                "SELECT sku, sale_price FROM chain_store_products WHERE chain = 'concung'"
            ))).all())
            assert listed == {"1": 150000, "3": None}
            changes = (await s.execute(text(
                "SELECT count(*) FROM chain_price_changes WHERE chain = 'concung' AND sku = '1'"
            ))).scalar_one()
            assert changes == 2, "the sale price changed once"
            assert await catalog.missing_stores(s, "concung", ["Con Cưng"]) == []

        # A third pass saved one category at a time: product 1 moves to the
        # later category. It must not be retired by the first save and come back.
        monkeypatch.setattr(catalog_worker, "CONCUNG_SAVE_EVERY", 1)
        pages["/sua-bot-1014.html"] = page([product("5", "50000", "50000")])
        pages["/bim-ta-1015.html"] = page([product("3", "300000", "300000"), product("1", "200000", "150000")])
        third = await catalog_worker._refresh_concung()
        assert third["removed"] == 0
        async with sm() as s:
            gone_1 = (await s.execute(text(
                "SELECT count(*) FROM chain_price_changes "
                "WHERE chain = 'concung' AND sku = '1' AND price IS NULL"
            ))).scalar_one()
            assert gone_1 == 0, "no false disappearance"
            menu_1 = (await s.execute(text(
                "SELECT menu FROM chain_store_products WHERE chain = 'concung' AND sku = '1'"
            ))).scalar_one()
            assert menu_1 == "bim-ta-1015"

        async with await _http() as c:
            r = await c.get("/api/v1/catalog/concung/products", headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["stores"] == 1 and body["total"] == 3
            assert {i["sku"]: i["min_price"] for i in body["items"]} == {"1": 150000, "3": 300000, "5": 50000}
            assert all(i["barcode"] is None and i["image_url"] is None for i in body["items"])
            r = await c.get("/api/v1/catalog/concung/stores", params={"sku": "1"}, headers={"X-API-Key": key})
            assert r.json()["master_ids"] == [], "the virtual store is no place"
    finally:
        async with sm() as s:
            for t in ("chain_price_daily", "chain_price_changes", "chain_store_products",
                      "chain_products", "chain_stores"):
                await s.execute(text(f"DELETE FROM {t} WHERE chain = 'concung'"))
            await s.commit()
            await _cleanup(s, tag)
