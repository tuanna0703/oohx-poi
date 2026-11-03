"""Brand clean-up: a dry run writes nothing; apply untags stores that do not
carry their brand, disables a generic "chain", and undo puts it all back."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from poi_lake.db import get_sessionmaker
from poi_lake.services import brand_cleanup, brand_quality
from poi_lake.services.brands import upsert_brand
from tests.integration.test_master_pois_api import _cleanup, _seed_master

pytestmark = pytest.mark.asyncio(loop_scope="function")


async def _tags(names: list[str]) -> dict[str, int]:
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            text("SELECT brand, count(*) FROM master_pois WHERE brand = ANY(:b) GROUP BY brand"), {"b": names}
        )).all()
    return {r[0]: r[1] for r in rows}


async def test_cleanup_plan_apply_and_undo() -> None:
    tag = "bc-" + uuid.uuid4().hex[:6]
    w = "Zorvex" + uuid.uuid4().hex[:4]
    generic = f"Phở Gia Truyền {w}"  # tagged through the alias "Gia Truyền"
    cinema = f"{w} Cinema"  # tagged through the alias "<w>"
    seeds = (
        [(generic, f"Nhà thuốc Gia Truyền {i}") for i in range(4)] + [(generic, f"Phở Gia Truyền {w}")]
        + [(cinema, f"{w} Cinema 1")] + [(cinema, f"Karaoke {w} {i}") for i in range(3)]
    )
    sm = get_sessionmaker()
    async with sm() as s:
        await upsert_brand(s, generic, ["Gia Truyền"], "hospitality")
        await upsert_brand(s, cinema, [w], "entertainment")
        for i, (brand, name) in enumerate(seeds):
            await _seed_master(s, name=name, lat=10.8, lng=106.7 + i / 100, test_tag=tag, brand=brand)
        await brand_quality.run(s, [generic, cinema])
        await s.commit()
    try:
        async with sm() as s:
            dry = await brand_cleanup.plan(s)
        mine = {b["brand"]: b for b in dry["brands"] if b["brand"] in (generic, cinema)}
        assert (mine[generic]["action"], mine[generic]["untag_masters"]) == ("disable", 5)
        assert (mine[cinema]["action"], mine[cinema]["untag_masters"]) == ("trim", 3)
        assert mine[cinema]["drop_aliases"] == [w]
        assert await _tags([generic, cinema]) == {generic: 5, cinema: 4}, "a dry run writes nothing"

        async with sm() as s:
            done = await brand_cleanup.apply(s)
            await s.commit()
        assert await _tags([generic, cinema]) == {cinema: 1}, "the real cinema keeps its tag"
        async with sm() as s:
            rows = dict((await s.execute(
                text("SELECT name, enabled FROM brands WHERE name = ANY(:b)"), {"b": [generic, cinema]}
            )).all())
            aliases = (await s.execute(text("SELECT aliases FROM brands WHERE name = :b"), {"b": cinema})).scalar_one()
        assert rows == {generic: False, cinema: True}
        assert list(aliases) == []

        async with sm() as s:
            back = await brand_cleanup.undo(s, done["run_id"])
            await s.commit()
        assert back["masters"] >= 8
        assert await _tags([generic, cinema]) == {generic: 5, cinema: 4}
        async with sm() as s:
            enabled = (await s.execute(text("SELECT enabled FROM brands WHERE name = :b"), {"b": generic})).scalar_one()
        assert enabled is True
    finally:
        async with sm() as s:
            await s.execute(text("DELETE FROM brand_quality WHERE brand = ANY(:b)"), {"b": [generic, cinema]})
            await s.execute(text("DELETE FROM brands WHERE name = ANY(:b)"), {"b": [generic, cinema]})
            await s.commit()
            await _cleanup(s, tag)
