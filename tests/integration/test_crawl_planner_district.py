"""District sweep: codes too rare for 5 km cells get one job per district.

On 2026-10-24, 84% of crawl jobs came back empty because the planner was
sweeping shopping malls cell by cell (0.46 raw POI per cell).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from poi_lake.db import get_sessionmaker
from poi_lake.db.models import IngestionJob


@pytest_asyncio.fixture(loop_scope="function")
async def district_row() -> AsyncIterator[dict]:
    suffix = uuid.uuid4().hex[:6]
    prov = f"D{suffix}"
    name = f"Tỉnh {suffix}"
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text(
                "INSERT INTO admin_units (code, name, level, lng_min, lat_min, lng_max, lat_max) "
                "VALUES (:c, :n, 1, 105.7, 20.9, 105.9, 21.1)"
            ),
            {"c": prov, "n": name},
        )
        for i, district in enumerate(("Quận Một", "Quận Hai", "Quận Ba")):
            await s.execute(
                text(
                    "INSERT INTO admin_units (code, name, level, parent_code, "
                    "lng_min, lat_min, lng_max, lat_max) "
                    "VALUES (:c, :n, 2, :p, :x1, 20.9, :x2, 21.0)"
                ),
                {"c": f"{prov}.{i}", "n": district, "p": prov,
                 "x1": 105.7 + i * 0.05, "x2": 105.75 + i * 0.05},
            )
        await s.execute(
            text(
                "INSERT INTO sources (code, name, adapter_class, config, enabled, priority) "
                "VALUES ('gosom_scraper', 'gosom', "
                "'poi_lake.adapters.gosom_scraper:GosomScraperAdapter', '{}'::jsonb, true, 30) "
                "ON CONFLICT (code) DO UPDATE SET enabled = true"
            )
        )
        plan_id = (
            await s.execute(
                text(
                    "INSERT INTO crawl_plan (province_code, openooh_code, cell_size_m, priority, "
                    "status, sweep) VALUES (:p, 'retail.shopping_malls', 5000, -2000, 'pending', "
                    "'district') RETURNING id"
                ),
                {"p": prov},
            )
        ).scalar_one()
        await s.commit()

    yield {"plan_id": int(plan_id), "province": prov, "name": name}

    async with sm() as s:
        await s.execute(
            text("DELETE FROM ingestion_jobs WHERE params->>'crawl_plan_id' = :i"),
            {"i": str(plan_id)},
        )
        await s.execute(text("DELETE FROM crawl_plan WHERE province_code = :p"), {"p": prov})
        await s.execute(text("DELETE FROM admin_units WHERE parent_code = :p"), {"p": prov})
        await s.execute(text("DELETE FROM admin_units WHERE code = :p"), {"p": prov})
        await s.commit()


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import poi_lake.workers.ingest as ingest

    monkeypatch.setattr(ingest.run_ingestion_job, "send", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from poi_lake.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "crawl_batch_size", 2, raising=False)
    monkeypatch.setattr(s, "crawl_planner_minutes", 10, raising=False)
    monkeypatch.setattr(s, "crawl_rate_per_hour", 200, raising=False)
    monkeypatch.setattr(s, "crawl_probe_cells", True, raising=False)


async def _drain() -> None:
    """Complete every in-flight job so the planner's in_flight guard stays open."""
    async with get_sessionmaker()() as s:
        await s.execute(
            text(
                "UPDATE ingestion_jobs SET status = 'completed', completed_at = NOW() "
                "WHERE status IN ('pending','running')"
            )
        )
        await s.commit()


async def _jobs(plan_id: int) -> list[IngestionJob]:
    async with get_sessionmaker()() as s:
        return list(
            (
                await s.execute(
                    select(IngestionJob)
                    .where(IngestionJob.params["crawl_plan_id"].astext == str(plan_id))
                    .order_by(IngestionJob.id)
                )
            ).scalars().all()
        )


async def _row(plan_id: int):  # type: ignore[no-untyped-def]
    async with get_sessionmaker()() as s:
        return (
            await s.execute(
                text(
                    "SELECT status, cells_total, cells_enqueued, cells_done, pois_raw "
                    "FROM crawl_plan WHERE id = :i"
                ),
                {"i": plan_id},
            )
        ).one()


async def test_one_job_per_district_with_its_name(district_row: dict) -> None:
    from poi_lake.workers.crawl_planner import _tick

    plan_id, name = district_row["plan_id"], district_row["name"]
    await _drain()
    stats = await _tick()
    assert stats["jobs_enqueued"] == 2 and stats["probes"] == 0, "no probing, budget of 2"
    await _drain()
    await _tick()

    jobs = await _jobs(plan_id)
    assert [j.params["place"] for j in jobs] == [
        f"Quận Một, {name}", f"Quận Hai, {name}", f"Quận Ba, {name}",
    ]
    assert all(j.params["sweep"] == "district" for j in jobs)
    assert all(j.params["radius_m"] >= 2000 for j in jobs)
    row = await _row(plan_id)
    assert (row.cells_total, row.cells_enqueued) == (3, 3)
    async with get_sessionmaker()() as s:
        cells = (
            await s.execute(
                text("SELECT count(*) FROM crawl_cells WHERE province_code = :p"),
                {"p": district_row["province"]},
            )
        ).scalar_one()
    assert cells == 0, "a district sweep needs no grid"


async def test_a_late_grid_job_does_not_count_as_a_district(district_row: dict) -> None:
    from poi_lake.services.ingestion import IngestionService
    from poi_lake.workers.crawl_planner import _tick

    plan_id = district_row["plan_id"]
    await _drain()
    await _tick()
    district_job = (await _jobs(plan_id))[0]
    grid_job = IngestionJob(
        source_id=district_job.source_id, job_type="area_sweep", status="completed",
        params={"lat": 21.0, "lng": 105.8, "radius_m": 2500,
                "category": "retail.shopping_malls", "crawl_plan_id": plan_id},
    )
    async with get_sessionmaker()() as s:
        svc = IngestionService(s)
        await svc._update_crawl_plan(grid_job, success=True, raw_count=5)
        assert (await _row(plan_id)).cells_done == 0, "grid job ignored"
        await svc._update_crawl_plan(district_job, success=True, raw_count=4)
    row = await _row(plan_id)
    assert (row.cells_done, row.pois_raw) == (1, 4)


async def test_brand_sweep_rows_search_the_chain_per_district(district_row: dict) -> None:
    from poi_lake.api.v1.crawl_plan import BrandSweepRequest, add_brand_sweeps, reprioritize
    from poi_lake.workers.crawl_planner import _tick

    prov, name = district_row["province"], district_row["name"]
    sm = get_sessionmaker()
    async with sm() as s:
        # Out of the way, so the brand row is the one the planner picks.
        await s.execute(
            text("UPDATE crawl_plan SET status = 'paused' WHERE id = :i"),
            {"i": district_row["plan_id"]},
        )
        await s.commit()
        res = await add_brand_sweeps(
            BrandSweepRequest(brands=["WinMart+"], province_codes=[prov], priority=-3000), s
        )
        assert res["rows_added"] == 1
        again = await add_brand_sweeps(
            BrandSweepRequest(brands=["WinMart+"], province_codes=[prov], priority=-3000), s
        )
        assert again["rows_added"] == 0, "re-posting adds nothing"
        await reprioritize(s)
        brand_id, prio, sweep = (
            await s.execute(
                text(
                    "SELECT id, priority, sweep FROM crawl_plan "
                    "WHERE province_code = :p AND openooh_code = 'brand:WinMart+'"
                ),
                {"p": prov},
            )
        ).one()
    assert (prio, sweep) == (-3000, "district"), "reprioritize leaves brand rows alone"

    await _drain()
    await _tick()
    jobs = await _jobs(int(brand_id))
    assert [(j.params["category"], j.params["place"]) for j in jobs] == [
        ("brand:WinMart+", f"Quận Một, {name}"), ("brand:WinMart+", f"Quận Hai, {name}"),
    ]
    async with sm() as s:
        await s.execute(
            text("DELETE FROM ingestion_jobs WHERE params->>'crawl_plan_id' = :i"),
            {"i": str(brand_id)},
        )
        await s.commit()
