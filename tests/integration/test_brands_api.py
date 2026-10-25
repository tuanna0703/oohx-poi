"""PUT /api/v1/brands/{name}: dictionary entry, re-tag of crawled rows, sweep rows."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from poi_lake.db import get_sessionmaker
from tests.integration.test_master_pois_api import _cleanup, _http, _make_client, _seed_master

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def test_put_brand_tags_existing_stores_and_queues_sweeps() -> None:
    sm = get_sessionmaker()
    tag = "brand-" + uuid.uuid4().hex[:6]
    brand = f"Soi Bien {tag}"  # unique per run; tokens are what the detector matches
    async with sm() as s:
        key, _ = await _make_client(s, tag, permissions=["write:brands"])
        reader, _ = await _make_client(s, tag)
        hit = await _seed_master(s, name=f"Hải sản Sói Biển {tag}", lat=21.03, lng=105.85, test_tag=tag)
        other = await _seed_master(s, name=f"Sói Xám {tag}", lat=21.03, lng=105.85, test_tag=tag)
        taken = await _seed_master(
            s, name=f"Sói Biển {tag}", lat=21.03, lng=105.85, test_tag=tag, brand="Circle K"
        )
        province = (await s.execute(text("SELECT code FROM admin_units WHERE level = 1 LIMIT 1"))).scalar_one()
    try:
        async with await _http() as c:
            body = {"search_terms": [f"Sói Biển {tag[-6:]}"], "category": "hospitality",
                    "province_codes": [province]}
            r = await c.put(f"/api/v1/brands/{brand}", json=body, headers={"X-API-Key": reader})
            assert r.status_code == 403, "read:master may not add brands"

            r = await c.put(f"/api/v1/brands/{brand}", json=body, headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["tagged"]["masters"] == 1
            assert out["sweep"] == {"rows_added": 1, "brands": 1, "provinces": 1}

            again = await c.put(f"/api/v1/brands/{brand}", json=body, headers={"X-API-Key": key})
            assert again.json()["sweep"]["rows_added"] == 0, "re-posting adds nothing"

            p = await c.get(
                f"/api/v1/brands/{brand}/progress",
                params={"search_terms": body["search_terms"]},
                headers={"X-API-Key": key},
            )
            assert p.json()["stores"] == 1
            assert p.json()["sweep_rows"] == 1

        async with sm() as s:
            brands = dict(
                (await s.execute(
                    text("SELECT id, brand FROM master_pois WHERE id = ANY(:ids)"),
                    {"ids": [hit, other, taken]},
                )).all()
            )
        assert brands == {hit: brand, other: None, taken: "Circle K"}
    finally:
        async with sm() as s:
            await s.execute(text("DELETE FROM crawl_plan WHERE openooh_code LIKE :c"), {"c": f"brand:%{tag[-6:]}"})
            await s.execute(text("DELETE FROM brands WHERE name = :b"), {"b": brand})
            await s.commit()
            await _cleanup(s, tag)
