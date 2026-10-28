"""Daily report: built for a day, upserted, served newest first."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import text

from poi_lake.db import get_sessionmaker
from poi_lake.services import reports
from tests.integration.test_master_pois_api import _cleanup, _http, _make_client

pytestmark = pytest.mark.asyncio(loop_scope="function")

DAY = date(2001, 1, 2)


def test_day_bounds_are_vietnam_midnights() -> None:
    start, end = reports.day_bounds(DAY)
    assert start.isoformat() == "2001-01-01T17:00:00+00:00"
    assert (end - start).days == 1


async def test_build_and_read_daily_report() -> None:
    sm = get_sessionmaker()
    tag = "rep-" + uuid.uuid4().hex[:6]
    async with sm() as s:
        key, _ = await _make_client(s, tag)
    try:
        async with sm() as s:
            m = await reports.build_daily_report(s, DAY)
            assert set(m) >= {"window", "crawl", "masters_active", "brands", "chains"}
            assert m["crawl"]["new_masters"] == 0, "nothing was created in 2001"
            assert set(m["chains"]) >= {"winmart", "bachhoaxanh"}
            assert m["chains"]["winmart"]["top_price_moves"] == []
            await reports.build_daily_report(s, DAY)  # upsert, no duplicate
            n = (await s.execute(text("SELECT count(*) FROM daily_reports WHERE day = :d"), {"d": DAY})).scalar_one()
            assert n == 1

        async with await _http() as c:
            r = await c.get("/api/v1/reports/daily", params={"days": 366}, headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            assert any(x["day"] == DAY.isoformat() for x in r.json()["reports"])

            r = await c.get("/api/v1/reports/growth", params={"days": 7}, headers={"X-API-Key": key})
            assert r.status_code == 200, r.text
            g = r.json()
            assert set(g) >= {"totals", "daily", "brands", "categories", "provinces", "wards", "new_brands"}
            assert len(g["daily"]) <= 7
            assert all(len(b["series"]) == reports.SERIES_DAYS for b in g["brands"])
    finally:
        async with sm() as s:
            await s.execute(text("DELETE FROM daily_reports WHERE day = :d"), {"d": DAY})
            await s.commit()
            await _cleanup(s, tag)
