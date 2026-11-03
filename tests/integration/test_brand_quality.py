"""The brand quality check records a verdict per brand and changes no tag."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from poi_lake.db import get_sessionmaker
from tests.integration.test_master_pois_api import _cleanup, _http, _make_client, _seed_master

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def test_a_detector_mistake_is_hidden_a_real_chain_kept_and_formats_merged() -> None:
    from poi_lake.services.brand_quality import run

    tag = "bq-" + uuid.uuid4().hex[:6]
    word = "Qualix" + uuid.uuid4().hex[:4]
    wrong = f"Phở {word}"  # tagged on shops that only share the second word
    chain = f"{word} Bank ATM"
    twin = f"{word} Bank"
    seeds = (
        [(wrong, f"Nhà thuốc {word} {i}", "retail.pharmacy") for i in range(4)]
        + [(wrong, f"Phở {word} Bát Đàn", "retail.pharmacy")]
        + [(chain, f"ATM {word} Bank {i}", "financial.atms") for i in range(3)]
        + [(twin, f"Ngân hàng {word} Bank {i}", "financial.banks") for i in range(2)]
    )
    sm = get_sessionmaker()
    async with sm() as s:
        for i, (brand, name, sub) in enumerate(seeds):
            mid = await _seed_master(s, name=name, lat=10.8, lng=106.7 + i / 100,
                                     test_tag=tag, brand=brand, category=sub.split(".")[0])
            await s.execute(text("UPDATE master_pois SET openooh_subcategory = :c WHERE id = :i"),
                            {"c": sub, "i": mid})
        await s.commit()
        token, _ = await _make_client(s, tag)
    try:
        async with sm() as s:
            await run(s, [wrong, chain, twin])
            await s.commit()
        async with await _http() as c:
            h = {"X-API-Key": token}
            items = (await c.get("/api/v1/brands/quality", headers=h)).json()["items"]
            q = {i["brand"]: i for i in items if i["brand"] in (wrong, chain, twin)}
            assert (q[wrong]["verdict"], q[wrong]["name_share"]) == ("hide", 0.2)
            assert (q[chain]["verdict"], q[chain]["industry"], q[chain]["confidence"]) == ("keep", "bank", "high")
            assert q[chain]["display_name"] == f"{word} Bank"
            assert q[twin]["merge_into"] == chain and q[chain]["merge_into"] is None

            rows = (await c.get("/api/v1/master-pois/brands?limit=500", headers=h)).json()
            mine = {r["brand"]: r for r in rows if r["brand"] in q}
            assert mine[wrong]["category"] == "retail.pharmacy", "category is unchanged"
            assert (mine[wrong]["verdict"], mine[twin]["merge_into"]) == ("hide", chain)
        async with sm() as s:
            still = (await s.execute(text("SELECT count(*) FROM master_pois WHERE brand = :b"), {"b": wrong})).scalar_one()
        assert still == 5, "recorded only: no tag is removed"
    finally:
        async with sm() as s:
            await s.execute(text("DELETE FROM brand_quality WHERE brand = ANY(:b)"), {"b": [wrong, chain, twin]})
            await s.commit()
            await _cleanup(s, tag)
