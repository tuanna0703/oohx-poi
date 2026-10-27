"""A brand's category in /master-pois/brands is the one most of its stores carry."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from poi_lake.db import get_sessionmaker
from tests.integration.test_master_pois_api import _cleanup, _http, _make_client, _seed_master

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def test_brand_category_is_the_most_common_one() -> None:
    tag = "bi-" + uuid.uuid4().hex[:6]
    brand = "Indus" + uuid.uuid4().hex[:4]
    sm = get_sessionmaker()
    async with sm() as s:
        for i, sub in enumerate(["retail.apparel", "retail.mother_baby", "retail.mother_baby", None]):
            mid = await _seed_master(s, name=f"{brand} {i}", lat=10.8, lng=106.7 + i / 100,
                                     test_tag=tag, brand=brand, category="retail")
            await s.execute(text("UPDATE master_pois SET openooh_subcategory = :c WHERE id = :i"), {"c": sub, "i": mid})
        await s.commit()
        token, _ = await _make_client(s, tag)
    try:
        async with await _http() as c:
            rows = (await c.get("/api/v1/master-pois/brands?limit=500", headers={"X-API-Key": token})).json()
        row = next(r for r in rows if r["brand"] == brand)
        assert (row["count"], row["category"]) == (4, "retail.mother_baby")
    finally:
        async with sm() as s:
            await _cleanup(s, tag)
