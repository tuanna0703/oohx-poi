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


async def test_price_moves_list_what_changed_against_the_day_before() -> None:
    from datetime import date

    sm = get_sessionmaker()
    tag = "pm-" + uuid.uuid4().hex[:6]
    chain = f"test-{tag}"
    d1, d2 = date(2026, 11, 7), date(2026, 11, 8)
    async with sm() as s:
        key, _ = await _make_client(s, tag)
        for sku, name in (("1", "Sữa A"), ("2", "Tã B"), ("3", "Bánh C"), ("4", "Mới D")):
            await s.execute(text(
                "INSERT INTO chain_products (chain, sku, name, name_folded, brand, category_path) "
                "VALUES (:c, :s, :n, :n, 'Brand', ARRAY['Nhóm', 'Loại'])"
            ), {"c": chain, "s": sku, "n": name})
        rows = [  # sku, day, mode price, regular, on_sale
            ("1", d1, 100000, 100000, 0), ("1", d2, 80000, 100000, 1),   # down 20%, promo starts
            ("2", d1, 200000, 200000, 0), ("2", d2, 210000, 210000, 0),  # up 5%
            ("3", d1, 50000, 60000, 1), ("3", d2, 50000, 50000, 0),      # same price, promo ends
            ("4", d2, 30000, 30000, 0),                                    # new: not a move
        ]
        for sku, day, price, regular, sale in rows:
            await s.execute(text(
                "INSERT INTO chain_price_daily (chain, sku, day, stores, on_sale, min_price, mode_price, "
                "max_price, regular_price) VALUES (:c, :s, :d, 1, :o, :p, :p, :p, :r)"
            ), {"c": chain, "s": sku, "d": day, "o": sale, "p": price, "r": regular})
        await s.commit()
    try:
        async with await _http() as c:
            h = {"X-API-Key": key}
            r = await c.get(f"/api/v1/catalog/{chain}/price-moves", headers=h)
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["day"] == "2026-11-08" and body["previous_day"] == "2026-11-07"
            assert (body["up"], body["down"], body["total"]) == (1, 1, 2)
            assert (body["promos_started"], body["promos_ended"]) == (1, 1)
            first, second = body["items"]
            assert (first["sku"], first["before"], first["after"], first["change"], first["change_pct"]) == (
                "1", 100000, 80000, -20000, -20.0
            ), "biggest move first"
            assert first["on_sale_after"] and not first["on_sale_before"] and first["category"] == "Loại"
            assert (second["sku"], second["change_pct"]) == ("2", 5.0)

            up = (await c.get(f"/api/v1/catalog/{chain}/price-moves", params={"direction": "up"},
                              headers=h)).json()
            assert [i["sku"] for i in up["items"]] == ["2"] and up["total"] == 1
            early = (await c.get(f"/api/v1/catalog/{chain}/price-moves", params={"day": "2026-11-07"},
                                 headers=h)).json()
            assert early["total"] == 0 and early["items"] == []
    finally:
        async with sm() as s:
            await s.execute(text("DELETE FROM chain_price_daily WHERE chain = :c"), {"c": chain})
            await s.execute(text("DELETE FROM chain_products WHERE chain = :c"), {"c": chain})
            await s.commit()
            await _cleanup(s, tag)


async def test_concung_rotation_gifts_and_stale_listings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gift categories are never read and what they listed goes; listings
    unseen for CONCUNG_STALE_DAYS go; each category's read time is kept; a run
    out of time reads nothing more and still saves."""
    import json as _json

    import httpx

    from poi_lake.catalog import concung
    from poi_lake.workers import catalog as catalog_worker

    def product(pid: str) -> dict:
        return {"id_product": pid, "product_name": f"Sản phẩm {pid}", "reference": pid,
                "manufacture_name": "Meiji", "price": "100000", "real_price": "100000",
                "category_name_erp": "Sữa"}

    def page(*pids: str) -> str:
        return "<html>" + "".join(
            f"<script>var productpid_1_{p}={_json.dumps(product(p))};</script>" for p in pids
        ) + "</html>"

    pages = {"/sua-bot-1014.html": page("1", "2"), "/bim-ta-1015.html": page("3"),
             "/qua-tang-khong-ban-101865.html": page("9")}
    sitemap = "<urlset>" + "".join(f"<url><loc>https://concung.com{p}</loc></url>" for p in pages) + "</urlset>"
    fetched: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        fetched.append(req.url.path)
        if req.url.path == "/sitemap.xml":
            return httpx.Response(200, text=sitemap)
        return httpx.Response(200, text=pages[req.url.path])

    real_client = concung.ConCungClient

    def fake_client(**kw):  # type: ignore[no-untyped-def]
        http = httpx.AsyncClient(base_url=concung.BASE_URL, transport=httpx.MockTransport(handler))
        return real_client(client=http, min_interval_s=0)

    monkeypatch.setattr(concung, "ConCungClient", fake_client)
    sm = get_sessionmaker()
    long_ago = datetime.now(UTC) - timedelta(days=20)
    async with sm() as s:
        store = await catalog.ensure_online_store(s, "concung", "Con Cưng")
        # Left by earlier runs: a gift (menu = its slug) seen yesterday, and a
        # product nobody has listed for 20 days.
        gift = concung.parse_product(product("9"), "qua-tang-khong-ban-101865")
        # #41 saved gifts under the page's heading instead of the slug.
        gift_named = concung.parse_product(product("10"), "Quà tặng không bán")
        old = concung.parse_product(product("old"), "sua-cu-1999")
        assert gift and gift_named and old
        await catalog.save_items(s, "concung", store, [old], answered_menus=[], now=long_ago)
        await catalog.save_items(s, "concung", store, [gift, gift_named], answered_menus=[],
                                 now=datetime.now(UTC) - timedelta(days=1))
    try:
        stats = await catalog_worker._refresh_concung()
        assert (stats["categories"], stats["read"], stats["stale_removed"]) == (2, 2, 1)
        assert "/qua-tang-khong-ban-101865.html" not in fetched, "gift category never read"
        async with sm() as s:
            skus = set((await s.execute(text(
                "SELECT sku FROM chain_store_products WHERE chain = 'concung'"))).scalars())
            assert skus == {"1", "2", "3"}, "the gifts (by slug and by name) and the stale listing are gone"
            state = await catalog.store_state(s, "concung", "online", catalog_worker.CONCUNG_READ_AT)
            assert set(state) == {"sua-bot-1014", "bim-ta-1015"}

        monkeypatch.setattr(catalog_worker, "CONCUNG_BUDGET_S", -1)
        fetched.clear()
        again = await catalog_worker._refresh_concung()
        assert again["out_of_time"] and again["read"] == 0 and again["removed"] == 0
        assert fetched == ["/sitemap.xml"]

        # Out of time after one category: product 2 left it (for a category
        # not read today, say). It must not be retired by that partial run.
        clock = iter([0.0, 0.0, 10.0])  # start, before category 1, before category 2
        monkeypatch.setattr(catalog_worker, "CONCUNG_BUDGET_S", 5)
        monkeypatch.setattr(catalog_worker, "_clock", lambda: next(clock, 10.0))
        pages["/bim-ta-1015.html"] = page("3")
        pages["/sua-bot-1014.html"] = page("1")
        partial = await catalog_worker._refresh_concung()
        assert partial["out_of_time"] and partial["read"] == 1 and partial["removed"] == 0
        async with sm() as s:
            still = (await s.execute(text(
                "SELECT count(*) FROM chain_store_products WHERE chain = 'concung' AND sku = '2'"
            ))).scalar_one()
            assert still == 1, "a partial run retires nothing per category"
    finally:
        async with sm() as s:
            for t in ("chain_price_daily", "chain_price_changes", "chain_store_products",
                      "chain_products", "chain_stores"):
                await s.execute(text(f"DELETE FROM {t} WHERE chain = 'concung'"))
            await s.commit()
