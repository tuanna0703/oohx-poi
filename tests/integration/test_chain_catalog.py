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
            assert await catalog.match_stores(s, chain, [brand]) == {"matched": 1}
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
    finally:
        async with sm() as s:
            await _drop_chain(s, chain)
            await _cleanup(s, tag)
