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
    # Whole budget to brand rows unless a test is about the split: rows left
    # in the database by other tests would otherwise take the category share.
    monkeypatch.setattr(s, "crawl_brand_share", 1.0, raising=False)
    monkeypatch.setattr(s, "crawl_brand_split_at", 15, raising=False)


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


async def test_brand_sweep_rows_search_the_chain_per_district(
    district_row: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from poi_lake.api.v1.crawl_plan import BrandSweepRequest, add_brand_sweeps, reprioritize
    from poi_lake.config import get_settings
    from poi_lake.workers.crawl_planner import _tick

    monkeypatch.setattr(get_settings(), "crawl_brand_province_first", False, raising=False)

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


async def _brand_rows(prov: str, terms: list[str]) -> list[int]:
    from poi_lake.services.brands import add_brand_sweeps

    async with get_sessionmaker()() as s:
        await s.execute(  # the category row out of the way unless a test wants it
            text("UPDATE crawl_plan SET status = 'paused' WHERE province_code = :p"), {"p": prov}
        )
        await add_brand_sweeps(s, terms, [prov], -3000)
        await s.commit()
        return [
            int(i)
            for (i,) in (
                await s.execute(
                    text(
                        "SELECT id FROM crawl_plan WHERE province_code = :p "
                        "AND openooh_code LIKE 'brand:%' ORDER BY id"
                    ),
                    {"p": prov},
                )
            ).all()
        ]


async def _forget_jobs(plan_ids: list[int]) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(
            text("DELETE FROM ingestion_jobs WHERE (params->>'crawl_plan_id')::bigint = ANY(:ids)"),
            {"ids": plan_ids},
        )
        await s.commit()


async def _sweep_of(plan_id: int) -> str:
    async with get_sessionmaker()() as s:
        return str(
            (await s.execute(text("SELECT sweep FROM crawl_plan WHERE id = :i"), {"i": plan_id})).scalar_one()
        )


async def test_brand_searches_the_province_then_splits_a_full_one(district_row: dict) -> None:
    """A chain in two cities should not cost 526 district searches."""
    from poi_lake.services.ingestion import IngestionService
    from poi_lake.workers.crawl_planner import _tick

    prov, name = district_row["province"], district_row["name"]
    (brand_id,) = await _brand_rows(prov, ["WinMart+"])
    try:
        await _drain()
        await _tick()
        (job,) = await _jobs(brand_id)
        assert (job.params["sweep"], job.params["place"]) == ("province", name)
        row = await _row(brand_id)
        assert (row.cells_total, row.cells_enqueued) == (1, 1)
        assert await _sweep_of(brand_id) == "province"

        async with get_sessionmaker()() as s:  # a full page came back
            svc = IngestionService(s)
            await svc._update_crawl_plan(job, success=True, raw_count=12)
            await svc._split_province(job, fetched=20)
        row = await _row(brand_id)
        assert await _sweep_of(brand_id) == "district"
        assert (row.status, row.cells_total, row.cells_enqueued, row.cells_done, row.pois_raw) == (
            "in_progress", 3, 0, 0, 12,
        )

        await _drain()
        await _tick()
        places = [j.params["place"] for j in (await _jobs(brand_id))[1:]]
        assert places == [f"Quận Một, {name}", f"Quận Hai, {name}"]
    finally:
        await _forget_jobs([brand_id])


async def test_a_province_search_that_is_not_full_ends_the_row(district_row: dict) -> None:
    from poi_lake.services.ingestion import IngestionService
    from poi_lake.workers.crawl_planner import _tick

    (brand_id,) = await _brand_rows(district_row["province"], ["Kohnan"])
    try:
        await _drain()
        await _tick()
        (job,) = await _jobs(brand_id)
        async with get_sessionmaker()() as s:
            svc = IngestionService(s)
            await svc._update_crawl_plan(job, success=True, raw_count=3)
            await svc._split_province(job, fetched=4)
        row = await _row(brand_id)
        assert (row.status, row.cells_done) == ("done", 1)
        assert await _sweep_of(brand_id) == "province"
    finally:
        await _forget_jobs([brand_id])


async def test_brand_sweeps_take_their_share_of_the_budget(
    district_row: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from poi_lake.config import get_settings
    from poi_lake.workers.crawl_planner import _tick

    s_ = get_settings()
    monkeypatch.setattr(s_, "crawl_batch_size", 10, raising=False)
    monkeypatch.setattr(s_, "crawl_brand_share", 0.3, raising=False)
    prov = district_row["province"]
    brand_ids = await _brand_rows(prov, ["A Mart", "B Mart", "C Mart", "D Mart", "E Mart"])
    async with get_sessionmaker()() as s:  # the category row has work too
        await s.execute(
            text("UPDATE crawl_plan SET status = 'pending' WHERE id = :i"), {"i": district_row["plan_id"]}
        )
        await s.commit()
    try:
        await _drain()
        stats = await _tick()
        assert stats["brand_jobs"] == 3, "30% of a budget of 10"
        assert stats["jobs_enqueued"] > 3, "the rest went to the category row"
    finally:
        await _forget_jobs(brand_ids)
